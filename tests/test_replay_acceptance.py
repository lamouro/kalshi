"""Acceptance checks from PAPER-ENGINE-SPEC §7 that can run on synthetic streams."""
from decimal import Decimal as D
from kalshi_engine.events import BookSnapshot, BookDelta, Trade, Lifecycle, Gap
from kalshi_engine.replay import L2Replay
from kalshi_engine.strategy import Strategy, Place


class OneShot(Strategy):
    def __init__(self, place):
        self.place, self.done = place, False

    def on_event(self, e, ctx):
        if not self.done and isinstance(e, BookSnapshot):
            self.done = True
            return [self.place]
        return []


def snap(ts=0, bids=None, asks=None, seq=1):
    return BookSnapshot("T", ts, bids or {D("0.40"): D(10)}, asks or {D("0.45"): D(5)}, seq=seq)


def test_check2_disappearing_quote_no_fill():
    # taker wants the 0.45 ask; it vanishes 50 ms later, before our 250 ms arrival
    s = OneShot(Place("T", "bid", D("0.45"), D(1), post_only=False))
    r = L2Replay(s).run([snap(0), BookDelta("T", 50, "ask", D("0.45"), D(-5), seq=2), Trade("T", 300, D("0.45"), D(1), "bid")])
    assert r.ledger.fills == []


def test_check3_depth_walk():
    s = OneShot(Place("T", "bid", D("0.50"), D(8), post_only=False))
    r = L2Replay(s).run([snap(0, asks={D("0.45"): D(2), D("0.47"): D(3)}), Trade("T", 400, D("0.40"), D(1), "ask")])
    assert sum(f.qty for f in r.ledger.fills) == D(5)
    vwap = sum(f.price * f.qty for f in r.ledger.fills) / D(5)
    assert vwap >= D("0.45")


def test_check5_dirty_book_blocks_orders():
    s = OneShot(Place("T", "bid", D("0.40"), D(1)))
    ev = [snap(0), Gap("T", 1, "seq gap")]
    # strategy fires on the snapshot; order arrives after the gap -> rejected
    r = L2Replay(s, latency_ms=10).run(ev + [Trade("T", 20, D("0.39"), D(5), "ask")])
    assert r.ledger.fills == [] and r.rejects == 1


def test_check8_resting_joins_behind_queue():
    s = OneShot(Place("T", "bid", D("0.40"), D(1)))
    ev = [snap(0), Trade("T", 300, D("0.40"), D(10), "ask"), Trade("T", 301, D("0.40"), D(1), "ask")]
    r = L2Replay(s).run(ev)
    assert len(r.ledger.fills) == 1 and r.ledger.fills[0].ts == 301


def test_check9_post_only_cross_rejected():
    s = OneShot(Place("T", "bid", D("0.45"), D(1), post_only=True))
    r = L2Replay(s).run([snap(0), Trade("T", 400, D("0.40"), D(1), "ask")])
    assert r.rejects == 1 and r.ledger.fills == []


def test_settlement_and_maker_fee_zero():
    s = OneShot(Place("T", "bid", D("0.40"), D(1)))
    ev = [snap(0, bids={D("0.39"): D(3)}), Trade("T", 300, D("0.40"), D(1), "ask"), Lifecycle("T", 500, "settle", "yes")]
    r = L2Replay(s).run(ev)
    assert r.ledger.fees == 0 and r.ledger.cash == D("0.60")
