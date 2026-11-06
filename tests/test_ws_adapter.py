"""Recorder JSONL -> events: mapping, per-sid seq checks, and an end-to-end L2 replay fill."""
import gzip, json
from decimal import Decimal as D
from kalshi_engine.events import BookSnapshot, BookDelta, Trade, Gap, Lifecycle
from kalshi_engine.ws_adapter import iter_events
from kalshi_engine.replay import L2Replay
from kalshi_engine.strategy import Strategy, Place


def frame(ns, m):
    return json.dumps({"conn_id": 1, "recv_mono_ns": ns, "recv_wall_ns": ns, "raw": json.dumps(m)})


def write(tmp, lines):
    p = tmp / "kalshi_20261106T01.jsonl.gz"
    with gzip.open(p, "wt") as f:
        f.write("\n".join(lines) + "\n")
    return [str(p)]


MS = 1_000_000


def stream():
    return [
        json.dumps({"type": "_connect", "conn_id": 1, "recv_wall_ns": 0, "tickers": 2}),
        frame(1 * MS, {"type": "orderbook_snapshot", "sid": 1, "seq": 1, "msg": {"market_ticker": "A",
              "yes_dollars": [["0.40", 10]], "no_dollars": [["0.55", 5]]}}),
        frame(2 * MS, {"type": "orderbook_snapshot", "sid": 1, "seq": 2, "msg": {"market_ticker": "B",
              "yes": [[30, 4]], "no": [[60, 4]]}}),
        frame(3 * MS, {"type": "orderbook_delta", "sid": 1, "seq": 3, "msg": {"market_ticker": "A",
              "price_dollars": "0.40", "delta": -3, "side": "yes"}}),
        frame(4 * MS, {"type": "orderbook_delta", "sid": 1, "seq": 4, "msg": {"market_ticker": "B",
              "price": 60, "delta_fp": "2.00", "side": "no"}}),
        frame(5 * MS, {"type": "trade", "sid": 2, "msg": {"market_ticker": "A", "yes_price_dollars": "0.45",
              "count": 2, "taker_side": "yes", "trade_id": "t1"}}),
        frame(6 * MS, {"type": "orderbook_delta", "sid": 1, "seq": 9, "msg": {"market_ticker": "A",
              "price_dollars": "0.41", "delta": 1, "side": "yes"}}),
        frame(7 * MS, {"type": "market_lifecycle_v2", "sid": 3, "msg": {"market_ticker": "A",
              "event_type": "determined", "result": "no"}}),
    ]


def test_mapping_and_seq(tmp_path):
    ev = list(iter_events(write(tmp_path, stream())))
    snapA = [e for e in ev if isinstance(e, BookSnapshot) and e.ticker == "A"][0]
    assert snapA.bids == {D("0.40"): D(10)} and snapA.asks == {D("0.45"): D(5)}
    snapB = [e for e in ev if isinstance(e, BookSnapshot) and e.ticker == "B"][0]
    assert snapB.bids == {D("0.30"): D(4)} and snapB.asks == {D("0.40"): D(4)}
    dB = [e for e in ev if isinstance(e, BookDelta) and e.ticker == "B"][0]
    assert dB.side == "ask" and dB.price == D("0.40") and dB.delta == D(2) and dB.seq is None
    tr = [e for e in ev if isinstance(e, Trade)][0]
    assert tr.taker_side == "bid" and tr.price == D("0.45")
    gaps = [e for e in ev if isinstance(e, Gap)]
    assert {g.ticker for g in gaps} == {"A", "B"}          # seq 4 -> 9 on sid 1 dirties both markets
    lc = [e for e in ev if isinstance(e, Lifecycle)][0]
    assert lc.kind == "settle" and lc.result == "no"


def test_filter_prefix(tmp_path):
    ev = list(iter_events(write(tmp_path, stream()), tickers=("B",)))
    assert all(e.ticker == "B" for e in ev)


class AskOnce(Strategy):
    name = "ask_once"
    def __init__(self): self.done = False
    def on_event(self, e, ctx):
        if not self.done and isinstance(e, BookSnapshot) and e.ticker == "A":
            self.done = True
            return [Place("A", "ask", D("0.45"), D(1), tag="ask_once")]
        return []


def test_end_to_end_queue_fill(tmp_path):
    # our ask joins behind 5 at 0.45 (arrives 250 ms later); then 6 lots trade at 0.45 -> 1 fills us
    lines = stream()[:2] + [
        frame(400 * MS, {"type": "trade", "sid": 2, "msg": {"market_ticker": "A", "yes_price_dollars": "0.45",
              "count": 6, "taker_side": "yes"}}),
    ]
    r = L2Replay(AskOnce(), phi_mode="floor").run(list(iter_events(write(tmp_path, lines))))
    assert [(f.side, f.price, f.qty) for f in r.ledger.fills] == [("ask", D("0.45"), D(1))]


def test_inflight_order_visible(tmp_path):
    class Spam(Strategy):
        name = "spam"
        def on_event(self, e, ctx):
            if isinstance(e, (BookSnapshot, BookDelta)) and e.ticker == "A":
                if not [o for o in ctx.open_orders.values() if o.tag == "spam"]:
                    return [Place("A", "bid", D("0.39"), D(1), tag="spam")]
            return []
    r = L2Replay(Spam()).run(list(iter_events(write(tmp_path, stream()[:4]))))
    assert len(r.orders) == 1


def test_truncated_file_is_a_gap_not_a_crash(tmp_path):
    p = write(tmp_path, stream()[:4])[0]
    data = open(p, "rb").read()
    open(p, "wb").write(data[:-12])                      # chop the gzip trailer, like a crash
    ev = list(iter_events([p]))
    assert any(isinstance(e, Gap) and e.reason == "truncated file" for e in ev)
