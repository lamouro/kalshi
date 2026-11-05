from decimal import Decimal as D
from kalshi_engine.kros import Kros, RestingOrder


def mk(side="bid", p="0.40", size="1"):
    return RestingOrder("x", "T", side, D(p), D(size), t_arrive=100)


def test_fill_needs_trade_reaching_past_queue():
    k = Kros("floor"); o = mk(); k.join(o, D(10))
    assert k.on_trade(o, 200, D("0.40"), D(10), "ask") is None      # clears queue exactly, no fill
    assert o.queue_ahead == 0
    f = k.on_trade(o, 201, D("0.40"), D(3), "ask")
    assert f[2] == D(1) and o.status == "filled"


def test_cancels_never_fill_and_are_capped():
    k = Kros("ceiling"); o = mk(); k.join(o, D(5))
    k.on_level_decrease(o, 200, "bid", D("0.40"), D(100), D(5))
    assert o.queue_ahead >= 0 and o.status == "resting" and o.filled == 0


def test_floor_ignores_cancels():
    k = Kros("floor"); o = mk(); k.join(o, D(5))
    k.on_level_decrease(o, 200, "bid", D("0.40"), D(4), D(5))
    assert o.queue_ahead == D(5)


def test_wrong_aggressor_side_does_not_fill():
    k = Kros("floor"); o = mk(); k.join(o, D(0))
    assert k.on_trade(o, 200, D("0.40"), D(5), "bid") is None        # taker bought YES: hits asks
    assert o.filled == 0


def test_trade_through_fills_at_our_price():
    k = Kros("floor"); o = mk("ask", "0.60"); k.join(o, D(50))
    f = k.on_trade(o, 200, D("0.62"), D(2), "bid")
    assert f[1] == D("0.60") and f[3] == "trade_through"


def test_not_active_before_arrival():
    k = Kros("floor"); o = mk(); k.join(o, D(0))
    assert k.on_trade(o, 99, D("0.39"), D(5), "ask") is None
