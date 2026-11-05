"""Kalshi V2 live gateway — DISABLED BY DEFAULT (PLAN rule 1: paper first, always).

Constructing it raises unless BOTH: env KALSHI_LIVE_APPROVED == 'yes-jackson-approved' and
allow_live=True is passed. V2 semantics (PAPER-ENGINE-SPEC §9): POST /portfolio/events/orders,
single YES book (side bid = buy YES, ask = sell YES / long NO), fixed-point dollar strings on the
price_ranges grid, required time_in_force and self_trade_prevention_type, client_order_id for
idempotency, exchange_index for crypto (shard 2) on every write.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from decimal import Decimal

REST = "https://api.elections.kalshi.com/trade-api/v2"
APPROVAL = "yes-jackson-approved"


class LiveDisabled(RuntimeError):
    pass


class LiveGateway:
    def __init__(self, signer, allow_live: bool = False, max_loss_dollars: Decimal = Decimal("100")):
        if not allow_live or os.environ.get("KALSHI_LIVE_APPROVED") != APPROVAL:
            raise LiveDisabled("Live trading is disabled until Jackson approves in writing (PLAN rule 1).")
        self.signer, self.max_loss = signer, Decimal(max_loss_dollars)

    def _req(self, method, path, body=None):
        url = REST + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={**self.signer.headers(method, "/trade-api/v2" + path),
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    @staticmethod
    def order_body(ticker, side, price: Decimal, count: Decimal, post_only=True, tif="GTC",
                   coid=None, exchange_index=None, expiration_time=None, order_group_id=None):
        body = {"market_ticker": ticker, "side": side, "price": f"{Decimal(price):.4f}",
                "count": f"{Decimal(count):.2f}", "time_in_force": tif,
                "self_trade_prevention_type": "maker" if post_only else "taker_at_cross",
                "post_only": post_only, "client_order_id": coid or str(uuid.uuid4())}
        if exchange_index is not None:
            body["exchange_index"] = exchange_index
        if expiration_time:
            body["expiration_time"] = expiration_time
        if order_group_id:
            body["order_group_id"] = order_group_id
        return body

    def place(self, **kw):
        return self._req("POST", "/portfolio/events/orders", self.order_body(**kw))

    def queue_position(self, order_id):
        return self._req("GET", f"/portfolio/orders/{order_id}/queue_position")
