import pytest
from decimal import Decimal as D
from kalshi_engine.live_gateway import LiveGateway, LiveDisabled


def test_live_gateway_refuses_without_approval(monkeypatch):
    monkeypatch.delenv("KALSHI_LIVE_APPROVED", raising=False)
    with pytest.raises(LiveDisabled):
        LiveGateway(signer=None, allow_live=True)


def test_v2_order_body_buy_no_is_ask():
    b = LiveGateway.order_body("T", "ask", D("0.6"), D("1"))
    assert b["side"] == "ask" and b["price"] == "0.6000" and b["count"] == "1.00"
    assert b["time_in_force"] == "GTC" and b["self_trade_prevention_type"] == "maker"
