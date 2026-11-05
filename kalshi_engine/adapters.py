"""Turn stored datasets into time-ordered engine events.

candles_to_events: Kalshi minute candlesticks (yes_bid / yes_ask / price OHLC, volume).
  Per candle (end ts T): synthetic prints at T-1 ms — the low as a taker SELL (hits bids) and
  the high as a taker BUY (hits asks), each with the candle volume — then a Quote at T with the
  closing bid/ask. Resting orders placed after seeing the quote at T can only fill on the next
  candle's prints. This is a PROXY: fills are bracket-only and sizes must stay small.
  Empty-book sentinels (bid 0 / ask 1) are mapped to None (acceptance check 4).
infer_result: last traded price >= 0.90 -> yes, <= 0.10 -> no, else None (dropped).
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal

from .events import Lifecycle, Tick, Trade
from .tape_sim import Quote

ZERO, ONE = Decimal("0"), Decimal("1")


def _d(x):
    return Decimal(x) if x not in (None, "") else None


def _clean(b, a):
    b = None if b is None or b <= ZERO else b
    a = None if a is None or a >= ONE else a
    return b, a


def iso_ms(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def infer_result(candles):
    for c in reversed(candles):
        p = _d(c.get("price", {}).get("close_dollars"))
        if p is not None:
            if p >= Decimal("0.90"):
                return "yes"
            if p <= Decimal("0.10"):
                return "no"
            return None
    return None


def candles_to_events(rec: dict, result: str | None = None):
    t = rec["ticker"]
    cs = rec["candles"]
    res = result or infer_result(cs)
    ev = []
    for c in cs:
        T = int(c["end_period_ts"]) * 1000
        vol = _d(c.get("volume_fp")) or ZERO
        pr = c.get("price", {})
        lo, hi = _d(pr.get("low_dollars")), _d(pr.get("high_dollars"))
        if vol > 0 and lo is not None:
            ev.append(Trade(t, T - 1, lo, vol, "ask"))
        if vol > 0 and hi is not None:
            ev.append(Trade(t, T - 1, hi, vol, "bid"))
        b, a = _clean(_d(c["yes_bid"].get("close_dollars")), _d(c["yes_ask"].get("close_dollars")))
        ev.append(Quote(t, T, b, a))
    close_ms = iso_ms(rec["close_time"])
    ev.append(Lifecycle(t, close_ms, "close"))
    if res:
        ev.append(Lifecycle(t, close_ms + 1, "settle", res))
    ev.sort(key=lambda e: e.ts)
    return ev, res


def trades_csv_to_events(path: str, ticker: str, close_time: str, result: str | None):
    """Trade tape CSV (created_time,yes_price,count,taker_book_side) -> events, oldest first."""
    ev = []
    with open(path) as f:
        for r in csv.DictReader(f):
            side = r.get("taker_book_side") or r.get("taker_side")
            if side in ("yes",):
                side = "bid"
            elif side in ("no",):
                side = "ask"
            ev.append(Trade(ticker, iso_ms(r["created_time"]), Decimal(r["yes_price"]),
                            Decimal(r["count"]), side))
    ev.sort(key=lambda e: e.ts)
    cm = iso_ms(close_time)
    ev.append(Lifecycle(ticker, cm, "close"))
    if result in ("yes", "no"):
        ev.append(Lifecycle(ticker, cm + 1, "settle", result))
    return ev


def trade_quotes(events, stale_ms=6 * 3600 * 1000):
    """Derive an approximate touch from the trade tape alone: the last taker-BUY print is taken
    as the ask, the last taker-SELL print as the bid (dropped after `stale_ms`, or when they
    cross). Emits a Quote after each trade. This is weaker than a recorded book; results using
    it are labelled 'tape-touch' and are bracket-only."""
    out, bid, ask, tb, ta = [], None, None, -1, -1
    for e in events:
        out.append(e)
        if isinstance(e, Trade):
            if e.taker_side == "bid":
                ask, ta = e.price, e.ts
            else:
                bid, tb = e.price, e.ts
            b = bid if bid is not None and e.ts - tb <= stale_ms else None
            a = ask if ask is not None and e.ts - ta <= stale_ms else None
            if b is not None and a is not None and b >= a:
                if e.taker_side == "bid":
                    b = None
                else:
                    a = None
            out.append(Quote(e.ticker, e.ts, b, a))
    return out
