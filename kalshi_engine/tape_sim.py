"""Historical tape simulator for data without a message-level book.

Inputs per market: a time-ordered stream of
  Trade      (from /markets/trades: price, count, taker side),
  Quote      (best bid/ask, e.g. from minute candlesticks or snapshots),
  Tick       (clock), Lifecycle('settle', result).
Without per-level depth the queue position is unknown, so resting fills are bracketed
(PLAN rule 10):
  floor   : fill only on a TRADE-THROUGH (print strictly better for the aggressor than our
            price). Equivalent to joining behind an infinite queue — a lower bound.
  ceiling : also fill on prints AT our price (front-of-queue) — an upper bound.
Taker orders fill at the prevailing quote only if a quote no older than `max_quote_age_ms`
exists (no phantom book: rule 3), and are charged the taker fee.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from .events import Lifecycle, Tick, Trade
from .fees import FeeAccumulator, SeriesFees
from .ledger import Fill, Ledger
from .strategy import Cancel, CancelAll, Ctx, Place

ZERO = Decimal("0")


@dataclass(frozen=True)
class Quote:
    ticker: str
    ts: int
    bid: Optional[Decimal]
    ask: Optional[Decimal]


@dataclass
class SimOrder:
    oid: str
    ticker: str
    side: str
    price: Decimal
    size: Decimal
    t_arrive: int
    tag: str
    filled: Decimal = ZERO
    status: str = "resting"
    acc: FeeAccumulator = field(default_factory=FeeAccumulator)

    @property
    def remaining(self):
        return self.size - self.filled


class TapeSim:
    def __init__(self, strategy, mode: str = "floor", latency_ms: int = 250,
                 fees: dict | None = None, max_quote_age_ms: int = 120_000,
                 markout_ms=(60_000, 600_000, 3_600_000)):
        assert mode in ("floor", "ceiling")
        self.s, self.mode, self.lat = strategy, mode, latency_ms
        self.fees = fees or {}
        self.max_age = max_quote_age_ms
        self.ledger = Ledger()
        self.ctx = Ctx()
        self.orders: dict[str, SimOrder] = {}
        self.quote_ts: dict[str, int] = {}
        self._ids = itertools.count(1)
        self.fill_rows: list[dict] = []        # for markouts
        self.mid_hist: dict[str, list] = {}    # ticker -> [(ts, mid)]
        self.markout_ms = markout_ms
        self.rejected = 0

    # ------------------------------------------------------------------ helpers
    def _sf(self, ticker) -> SeriesFees:
        return self.fees.get(ticker.split("-")[0], SeriesFees())

    def _fill(self, o: SimOrder, ts: int, price: Decimal, qty: Decimal, taker: bool, cause: str):
        qty = min(qty, o.remaining)
        if qty <= 0:
            return
        fee = o.acc.charge(price, qty, taker, self._sf(o.ticker))
        o.filled += qty
        if o.remaining <= 0:
            o.status = "filled"
            self.ctx.open_orders.pop(o.oid, None)
        f = Fill(ts, o.ticker, o.side, price, qty, fee, taker, o.tag, cause, o.oid)
        self.ledger.record(f)
        self.ctx.positions[o.ticker] = self.ledger.position(o.ticker)
        self.fill_rows.append({"ts": ts, "ticker": o.ticker, "side": o.side, "price": price,
                               "qty": qty, "fee": fee, "taker": taker, "tag": o.tag, "cause": cause})

    def _exec(self, cmds, ts: int):
        for c in cmds or []:
            if isinstance(c, Place):
                q = self.ctx.quotes.get(c.ticker, (None, None))
                fresh = self.quote_ts.get(c.ticker, -10**18) >= ts - self.max_age
                crosses = (c.side == "bid" and q[1] is not None and c.price >= q[1]) or \
                          (c.side == "ask" and q[0] is not None and c.price <= q[0])
                oid = c.coid or f"o{next(self._ids)}"
                o = SimOrder(oid, c.ticker, c.side, Decimal(c.price), Decimal(c.size), ts + self.lat, c.tag)
                if crosses:
                    if c.post_only:
                        self.rejected += 1
                        continue
                    if not fresh:
                        self.rejected += 1
                        continue
                    px = q[1] if c.side == "bid" else q[0]
                    self.orders[oid] = o
                    self._fill(o, ts + self.lat, px, o.size, True, "taker")
                    o.status = "filled" if o.remaining <= 0 else "cancelled"   # IOC semantics
                    continue
                if not c.post_only and not crosses and c.tag.endswith(":ioc"):
                    continue
                self.orders[oid] = o
                self.ctx.open_orders[oid] = o
            elif isinstance(c, Cancel):
                o = self.orders.get(c.oid)
                if o and o.status == "resting":
                    o.status = "cancelled"
                    self.ctx.open_orders.pop(o.oid, None)
            elif isinstance(c, CancelAll):
                for o in list(self.ctx.open_orders.values()):
                    if (c.ticker is None or o.ticker == c.ticker) and (c.tag is None or o.tag == c.tag):
                        o.status = "cancelled"
                        self.ctx.open_orders.pop(o.oid, None)

    # ------------------------------------------------------------------ main loop
    def run(self, events: Iterable):
        for e in events:
            ts = e.ts
            self.ctx.now = ts
            if isinstance(e, Quote):
                self.ctx.quotes[e.ticker] = (e.bid, e.ask)
                self.quote_ts[e.ticker] = ts
                if e.bid is not None and e.ask is not None:
                    self.mid_hist.setdefault(e.ticker, []).append((ts, (e.bid + e.ask) / 2))
            elif isinstance(e, Trade):
                self._on_trade(e)
                self.mid_hist.setdefault(e.ticker, []).append((ts, e.price))   # last-trade proxy
            elif isinstance(e, Lifecycle):
                if e.kind == "close":
                    for o in list(self.ctx.open_orders.values()):
                        if o.ticker == e.ticker:
                            o.status = "cancelled"
                            self.ctx.open_orders.pop(o.oid, None)
                if e.kind == "settle" and e.result:
                    for o in list(self.ctx.open_orders.values()):
                        if o.ticker == e.ticker:
                            o.status = "cancelled"
                            self.ctx.open_orders.pop(o.oid, None)
                    self.ledger.settle(e.ticker, e.result)
                    self.ctx.positions[e.ticker] = ZERO
                    for r in self.fill_rows:
                        if r["ticker"] == e.ticker and "settle" not in r:
                            r["settle"] = Decimal(1) if e.result == "yes" else Decimal(0)
            self._exec(self.s.on_event(e, self.ctx), ts)
        return self

    def _on_trade(self, t: Trade):
        for o in list(self.ctx.open_orders.values()):
            if o.ticker != t.ticker or o.status != "resting" or t.ts < o.t_arrive:
                continue
            hits = (o.side == "bid" and t.taker_side == "ask") or (o.side == "ask" and t.taker_side == "bid")
            if not hits:
                continue
            through = (o.side == "bid" and t.price < o.price) or (o.side == "ask" and t.price > o.price)
            at = t.price == o.price
            if through:
                self._fill(o, t.ts, o.price, t.count, False, "trade_through")
            elif at and self.mode == "ceiling":
                self._fill(o, t.ts, o.price, t.count, False, "trade_at_price")

    # ------------------------------------------------------------------ reporting
    def markouts(self):
        """Per-fill signed markouts (cents, from OUR side) at each horizon and at settlement."""
        import bisect
        out = []
        for r in self.fill_rows:
            sign = 1 if r["side"] == "bid" else -1
            row = dict(r)
            hist = self.mid_hist.get(r["ticker"], [])
            tss = [h[0] for h in hist]
            for h in self.markout_ms:
                i = bisect.bisect_right(tss, r["ts"] + h) - 1
                row[f"mo_{h//1000}s"] = float(sign * (hist[i][1] - r["price"]) * 100) if i >= 0 and hist[i][0] > r["ts"] else None
            if "settle" in r:
                row["mo_settle"] = float(sign * (r["settle"] - r["price"]) * 100)
            out.append(row)
        return out
