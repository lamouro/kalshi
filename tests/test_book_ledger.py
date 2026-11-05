from decimal import Decimal as D
from kalshi_engine.book import Book
from kalshi_engine.events import BookSnapshot, BookDelta
from kalshi_engine.ledger import Ledger, Fill


def test_seq_gap_marks_dirty_and_snapshot_clears():
    b = Book("T")
    assert b.dirty
    b.apply_snapshot(BookSnapshot("T", 1, {D("0.40"): D(10)}, {D("0.45"): D(5)}, seq=1))
    assert not b.dirty and b.best_bid() == D("0.40") and b.best_ask() == D("0.45")
    b.apply_delta(BookDelta("T", 2, "bid", D("0.40"), D(-3), seq=3))
    assert b.dirty


def test_walk_never_exceeds_depth():
    b = Book("T"); b.apply_snapshot(BookSnapshot("T", 1, {}, {D("0.45"): D(2), D("0.46"): D(3)}, seq=1))
    fills = b.walk("bid", D("0.50"), D(10))
    assert sum(q for _, q in fills) == D(5) and fills[0][0] == D("0.45")


def test_yes_no_symmetry():
    a, b = Ledger(), Ledger()
    a.record(Fill(1, "T", "bid", D("0.07"), D(1), D(0), False)); a.settle("T", "yes")
    b.record(Fill(1, "U", "ask", D("0.93"), D(1), D(0), False)); b.settle("U", "no")
    assert a.cash == b.cash == D("0.93")


def test_round_trip_flat_position():
    l = Ledger()
    l.record(Fill(1, "T", "bid", D("0.40"), D(2), D(0), False))
    l.record(Fill(2, "T", "ask", D("0.45"), D(2), D(0), False))
    assert l.position("T") == 0 and l.cash == D("0.10")
