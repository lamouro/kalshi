"""L2 replay gateway: KROS on a recorded message-level stream (snapshots + deltas + trades).

This is the high-fidelity path (PAPER-ENGINE-SPEC §4) once the recorder has data. Rules:
  * decisions see the book as received; orders arrive at t + latency (sampled from measured
    latencies when available);
  * marketable orders walk the book AT ARRIVAL (never the decision snapshot) and pay taker fee;
  * post-only orders that would cross are rejected (PostOnlyCrossCancel);
  * resting orders join behind the full level (KROS);
  * deltas at our level not explained by a same-clock trade are cancels (phi bracket);
  * a dirty book (seq gap) blocks new orders and flags resting phantoms;
  * a phantom fill is capped so we never take more than the printed volume.
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from decimal import Decimal

from .book import Book
from .events import BookDelta, BookSnapshot, Gap, Lifecycle, Trade
from .fees import FeeAccumulator, SeriesFees
from .kros import Kros, RestingOrder
from .ledger import Fill, Ledger
from .strategy import Cancel, CancelAll, Ctx, Place

ZERO = Decimal("0")


class L2Replay:
    def __init__(self, strategy, phi_mode="floor", latency_ms=250, fees=None, phi_const="0.5"):
        self.s = strategy
        self.kros = Kros(phi_mode, Decimal(phi_const))
        self.lat = latency_ms
        self.fees = fees or {}
        self.books: dict[str, Book] = {}
        self.ledger = Ledger()
        self.ctx = Ctx()
        self.orders: dict[str, RestingOrder] = {}
        self.pending: list = []                  # (t_arrive, Place, oid)
        self.accs: dict[str, FeeAccumulator] = {}
        self.tags: dict[str, str] = {}
        self._ids = itertools.count(1)
        self._clock_trades = defaultdict(lambda: ZERO)   # (ticker, ts, side, price) -> traded vol
        self.fill_rows, self.rejects, self.dirty_flags = [], 0, 0

    def _sf(self, t):
        return self.fees.get(t.split("-")[0], SeriesFees())

    def _record(self, o, ts, px, q, taker, cause):
        fee = self.accs[o.oid].charge(px, q, taker, self._sf(o.ticker))
        f = Fill(ts, o.ticker, o.side, px, q, fee, taker, self.tags[o.oid], cause, o.oid)
        self.ledger.record(f)
        self.ctx.positions[o.ticker] = self.ledger.position(o.ticker)
        self.fill_rows.append(dict(ts=ts, ticker=o.ticker, side=o.side, price=px, qty=q, fee=fee,
                                   taker=taker, tag=self.tags[o.oid], cause=cause))

    def _arrive(self, ts):
        due = [p for p in self.pending if p[0] <= ts]
        self.pending = [p for p in self.pending if p[0] > ts]
        for t_arr, c, oid in due:
            o = self.orders.get(oid)
            if o is None or o.status != "pending":        # cancelled while in flight
                continue
            b = self.books.get(c.ticker)
            if b is None or b.dirty:
                self.rejects += 1
                o.status = "rejected"; self.ctx.open_orders.pop(oid, None)
                continue
            o.status = "resting"
            self.accs[oid] = FeeAccumulator()
            self.tags[oid] = c.tag
            opp_best = b.best_ask() if c.side == "bid" else b.best_bid()
            crosses = opp_best is not None and ((c.side == "bid" and o.price >= opp_best) or (c.side == "ask" and o.price <= opp_best))
            if crosses:
                self.ctx.open_orders.pop(oid, None)
                if c.post_only:
                    self.rejects += 1
                    o.status = "rejected"
                    continue
                for px, q in b.walk(c.side, o.price, o.size):
                    o.filled += q
                    self._record(o, t_arr, px, q, True, "taker")
                o.status = "filled" if o.filled >= o.size else "cancelled"   # IOC remainder dropped
                continue
            self.kros.join(o, b.size_at(c.side, o.price))

    def _drop(self, o):
        if o.status == "pending":
            o.status = "cancelled"
        else:
            self.kros.cancel(o)
        self.ctx.open_orders.pop(o.oid, None)

    def _exec(self, cmds, ts):
        for c in cmds or []:
            if isinstance(c, Place):
                oid = c.coid or f"o{next(self._ids)}"
                o = RestingOrder(oid, c.ticker, c.side, Decimal(c.price), Decimal(c.size), ts + self.lat,
                                 status="pending", tag=c.tag)
                self.orders[oid] = o
                self.ctx.open_orders[oid] = o            # visible to the strategy while in flight
                self.pending.append((ts + self.lat, c, oid))
            elif isinstance(c, Cancel):
                o = self.orders.get(c.oid)
                if o and o.oid in self.ctx.open_orders:
                    self._drop(o)
            elif isinstance(c, CancelAll):
                for o in list(self.ctx.open_orders.values()):
                    if (c.ticker is None or o.ticker == c.ticker) and (c.tag is None or o.tag == c.tag):
                        self._drop(o)

    def run(self, events):
        for e in events:
            ts = e.ts
            self.ctx.now = ts
            self._arrive(ts)
            t = getattr(e, "ticker", None)
            if isinstance(e, BookSnapshot):
                self.books.setdefault(t, Book(t)).apply_snapshot(e)
            elif isinstance(e, BookDelta):
                b = self.books.setdefault(t, Book(t))
                before = b.size_at(e.side, e.price)
                b.apply_delta(e)
                if e.delta < 0 and e.client_order_id is None:
                    traded = self._clock_trades.pop((t, e.clock or ts, e.side, e.price), ZERO)
                    cancel_part = -e.delta - traded
                    if cancel_part > 0:
                        for o in list(self.ctx.open_orders.values()):
                            if o.ticker == t and o.status == "resting":
                                self.kros.on_level_decrease(o, ts, e.side, e.price, cancel_part, before)
                if b.dirty:
                    self.dirty_flags += 1
            elif isinstance(e, Trade):
                hit_side = "bid" if e.taker_side == "ask" else "ask"
                self._clock_trades[(t, e.clock or ts, hit_side, e.price)] += e.count
                for o in list(self.ctx.open_orders.values()):
                    if o.ticker == t and o.status == "resting":
                        f = self.kros.on_trade(o, ts, e.price, e.count, e.taker_side)
                        if f:
                            o.filled -= f[2]               # _record re-adds via ledger only
                            o.filled += f[2]
                            self._record(o, f[0], f[1], f[2], False, f[3])
                            if o.status == "filled":
                                self.ctx.open_orders.pop(o.oid, None)
            elif isinstance(e, Gap):
                self.books.setdefault(t, Book(t)).mark_dirty(e.reason)
                self.dirty_flags += 1
            elif isinstance(e, Lifecycle):
                if e.kind in ("close", "pause", "settle"):
                    for o in list(self.ctx.open_orders.values()):
                        if o.ticker == t:
                            self._drop(o)
                if e.kind == "settle" and e.result:
                    self.ledger.settle(t, e.result)
                    for r in self.fill_rows:
                        if r["ticker"] == t and "settle" not in r:
                            r["settle"] = Decimal(1) if e.result == "yes" else Decimal(0)
            b = self.books.get(t) if t else None
            if b is not None:
                self.ctx.quotes[t] = (b.best_bid(), b.best_ask())
            self._exec(self.s.on_event(e, self.ctx), ts)
        return self
