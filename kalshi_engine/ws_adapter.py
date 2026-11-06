"""Recorder output (recorder/ws_recorder.py hourly gzip JSONL) -> normalised engine events.

Each line is either a recorder marker ({"type": "_connect" | "_gap" | "_disconnect", ...}) or a raw
Kalshi WS v2 frame wrapped as {"conn_id", "recv_mono_ns", "recv_wall_ns", "raw": "<json>"}.

Mapping onto the canonical YES book (events.py):
  * orderbook_snapshot: msg.yes levels -> bids at p; msg.no levels at q -> asks at 1-q
  * orderbook_delta:    side 'yes' -> bid ladder at p; side 'no' at q -> ask ladder at 1-q
  * trade:              taker_side 'yes' (bought YES, lifted asks) -> 'bid'; 'no' -> 'ask'
  * market_lifecycle_v2: determined/settled -> Lifecycle('settle', result); closed/deactivated
    -> 'close'/'pause'; activated -> 'resume'
Field names drift between API versions, so every number accepts the *_dollars / *_fp string form
first and the legacy integer-cents / integer-count form second.

Integrity: Kalshi's seq is per subscription (sid), not per market, so it is checked HERE, per sid,
and BookDelta is emitted with seq=None (Book would otherwise see false gaps). A seq break, a
recorder _gap / _disconnect, or a new connection marks every market DIRTY (Gap) until its next
snapshot. Timestamps are local receive time in ms (exchange ts when the frame carries one is kept
as `clock` bucket input). `clock` = receive ms // CLOCK_MS so a trade and the delta it caused,
which arrive as separate frames a few ms apart, match in L2Replay's cancel attribution.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
from collections import Counter
from decimal import Decimal

from .events import BookDelta, BookSnapshot, Gap, Lifecycle, Trade

ONE = Decimal("1")
CLOCK_MS = 100


def _px(d: dict, key: str):
    """Price in dollars from '<key>_dollars' (string) or '<key>' (int cents)."""
    for k in (f"{key}_dollars", f"{key}_dollars_fp"):
        if d.get(k) is not None:
            return Decimal(str(d[k]))
    if d.get(key) is not None:
        v = d[key]
        return Decimal(str(v)) if isinstance(v, str) and "." in v else Decimal(int(v)) / 100
    return None


def _qty(d: dict, key: str):
    for k in (f"{key}_fp",):
        if d.get(k) is not None:
            return Decimal(str(d[k]))
    return Decimal(str(d[key])) if d.get(key) is not None else None


def _levels(msg: dict, side: str):
    """Snapshot ladder for 'yes' or 'no' as [(price_dollars, qty)]."""
    for k in (f"{side}_dollars_fp", f"{side}_dollars"):
        if msg.get(k):
            return [(Decimal(str(p)), Decimal(str(q))) for p, q in msg[k]]
    if msg.get(side):
        return [(Decimal(int(p)) / 100, Decimal(str(q))) for p, q in msg[side]]
    return []


def files(root: str, start: str = "", end: str = "~"):
    """Hourly files in order; start/end are 'YYYYMMDDTHH' bounds (inclusive)."""
    out = []
    for p in sorted(glob.glob(os.path.join(root, "kalshi_*.jsonl.gz"))):
        h = os.path.basename(p)[7:18]
        if start <= h <= end:
            out.append(p)
    return out


def iter_events(paths, tickers=None, stats: Counter | None = None):
    """Yield engine events from recorder files, in receive order. `tickers` filters markets
    (a set, a prefix tuple, or None for all). `stats` collects parse counts for audits."""
    st = stats if stats is not None else Counter()
    last_seq: dict = {}
    sid_tickers: dict = {}
    seen: set = set()

    def want(t):
        if t is None:
            return False
        if tickers is None:
            return True
        if isinstance(tickers, tuple):
            return t.startswith(tickers)
        return t in tickers

    def dirty_all(ts, reason):
        for t in sorted(seen):
            yield Gap(t, ts, reason)

    for p in paths:
        with gzip.open(p, "rt") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except Exception:
                    st["bad_line"] += 1
                    continue
                ts = int(item.get("recv_wall_ns", 0) // 1_000_000)
                typ = item.get("type")
                if typ in ("_connect", "_gap", "_disconnect"):
                    st[typ] += 1
                    last_seq.clear()
                    yield from dirty_all(ts, typ)
                    continue
                raw = item.get("raw")
                if raw is None:
                    st["no_raw"] += 1
                    continue
                try:
                    m = json.loads(raw)
                except Exception:
                    st["bad_raw"] += 1
                    continue
                mt, msg, sid, seq = m.get("type"), m.get("msg") or {}, m.get("sid"), m.get("seq")
                t = msg.get("market_ticker")
                clock = ts // CLOCK_MS
                if mt == "orderbook_snapshot":
                    if sid is not None and seq is not None:
                        last_seq[sid] = seq
                        sid_tickers.setdefault(sid, set()).add(t)
                    st["snapshot"] += 1
                    if not want(t):
                        continue
                    seen.add(t)
                    bids = {p_: q for p_, q in _levels(msg, "yes") if q > 0}
                    asks = {ONE - p_: q for p_, q in _levels(msg, "no") if q > 0}
                    yield BookSnapshot(t, ts, bids, asks, seq=None, clock=clock, recv_ns=item.get("recv_wall_ns"))
                    continue
                if mt == "orderbook_delta":
                    st["delta"] += 1
                    if sid is not None and seq is not None:
                        if sid in last_seq and seq != last_seq[sid] + 1:
                            st["seq_break"] += 1
                            for tt in sorted(sid_tickers.get(sid, ())):
                                if want(tt):
                                    yield Gap(tt, ts, f"seq {last_seq[sid]}->{seq}")
                        last_seq[sid] = seq
                        sid_tickers.setdefault(sid, set()).add(t)
                    if not want(t):
                        continue
                    seen.add(t)
                    p_, dq, side = _px(msg, "price"), _qty(msg, "delta"), msg.get("side")
                    if p_ is None or dq is None or side not in ("yes", "no"):
                        st["delta_unparsed"] += 1
                        continue
                    if side == "yes":
                        yield BookDelta(t, ts, "bid", p_, dq, None, clock, msg.get("client_order_id"), item.get("recv_wall_ns"))
                    else:
                        yield BookDelta(t, ts, "ask", ONE - p_, dq, None, clock, msg.get("client_order_id"), item.get("recv_wall_ns"))
                    continue
                if mt == "trade":
                    st["trade"] += 1
                    if not want(t):
                        continue
                    p_ = _px(msg, "yes_price")
                    c = _qty(msg, "count")
                    ts_side = msg.get("taker_side")
                    if p_ is None or c is None or ts_side not in ("yes", "no"):
                        st["trade_unparsed"] += 1
                        continue
                    yield Trade(t, ts, p_, c, "bid" if ts_side == "yes" else "ask", msg.get("trade_id"), clock)
                    continue
                if mt == "market_lifecycle_v2":
                    st["lifecycle"] += 1
                    if not want(t):
                        continue
                    ev = msg.get("event_type", "")
                    res = msg.get("result")
                    res = res if res in ("yes", "no") else None
                    kind = {"determined": "settle", "settled": "settle", "closed": "close",
                            "deactivated": "pause", "activated": "resume", "created": "open"}.get(ev)
                    if kind:
                        yield Lifecycle(t, ts, kind, res if kind == "settle" else None)
                    continue
                st[f"other:{mt}"] += 1
