"""KROS v0 — Kalshi Resting-Order Simulator (PAPER-ENGINE-SPEC §4), modelled on dove fill_sim.

A phantom resting order at YES price p on side s:
  * joins BEHIND the full displayed level at arrival (A0 = displayed size);
  * trades at p against our side consume the queue ahead first; only volume reaching PAST the
    queue fills us (a fill needs a trade — dove predicate TRADE_REACHES_ORDER);
  * a trade-through (print strictly better for the aggressor than p) fills us, price priority;
  * an unexplained decrease at p is a cancel: A -= min(A, d * phi). Cancels never fill.
  * fills are always at p.
phi modes give the bracket: 'floor' (0, trades only), 'ceiling' (proportional A/(D - S_own)),
or a calibrated constant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

ZERO = Decimal("0")


@dataclass
class RestingOrder:
    oid: str
    ticker: str
    side: str                # 'bid' (buy YES) or 'ask' (sell YES)
    price: Decimal
    size: Decimal
    t_arrive: int
    queue_ahead: Decimal = ZERO
    filled: Decimal = ZERO
    status: str = "resting"  # resting | filled | cancelled
    fills: list = field(default_factory=list)   # (ts, price, qty, cause)

    @property
    def remaining(self) -> Decimal:
        return self.size - self.filled


class Kros:
    def __init__(self, phi_mode: str = "floor", phi_const: Decimal = Decimal("0.5")):
        assert phi_mode in ("floor", "ceiling", "const")
        self.phi_mode, self.phi_const = phi_mode, Decimal(phi_const)

    # ---------------------------------------------------------------- lifecycle
    def join(self, o: RestingOrder, displayed_at_price: Decimal) -> None:
        o.queue_ahead = Decimal(displayed_at_price)

    def _fill(self, o: RestingOrder, ts: int, qty: Decimal, cause: str):
        q = min(qty, o.remaining)
        if q <= 0:
            return None
        o.filled += q
        o.fills.append((ts, o.price, q, cause))
        if o.remaining <= 0:
            o.status = "filled"
        return (ts, o.price, q, cause)

    # ---------------------------------------------------------------- events
    def on_trade(self, o: RestingOrder, ts: int, price: Decimal, count: Decimal, taker_side: str):
        """taker_side 'ask' = taker sold YES, hits BIDS; 'bid' = taker bought YES, hits ASKS."""
        if o.status != "resting" or ts < o.t_arrive:
            return None
        hits_us = (o.side == "bid" and taker_side == "ask") or (o.side == "ask" and taker_side == "bid")
        if not hits_us:
            return None
        if price == o.price:
            if count > o.queue_ahead:
                over = count - o.queue_ahead
                o.queue_ahead = ZERO
                return self._fill(o, ts, over, "trade_at_price")
            o.queue_ahead -= count
            return None
        better_for_aggressor = (o.side == "bid" and price < o.price) or (o.side == "ask" and price > o.price)
        if better_for_aggressor:
            o.queue_ahead = ZERO
            return self._fill(o, ts, count, "trade_through")
        return None

    def on_level_decrease(self, o: RestingOrder, ts: int, side: str, price: Decimal,
                          decrease: Decimal, level_depth_before: Decimal, own_size: Decimal = ZERO):
        """Unexplained (non-trade) decrease at our level = cancels. Never fills."""
        if o.status != "resting" or ts < o.t_arrive or side != o.side or price != o.price:
            return
        if self.phi_mode == "floor":
            phi = ZERO
        elif self.phi_mode == "ceiling":
            denom = level_depth_before - own_size
            phi = (o.queue_ahead / denom) if denom > 0 else ZERO
        else:
            phi = self.phi_const
        o.queue_ahead -= min(o.queue_ahead, decrease * phi)

    def on_cross(self, o: RestingOrder, ts: int, opposite_best: Optional[Decimal], crossing_size: Decimal):
        """Opposite ladder moved through our price with no print on that clock: tagged fill."""
        if o.status != "resting" or ts < o.t_arrive or opposite_best is None:
            return None
        crossed = (o.side == "bid" and opposite_best <= o.price) or (o.side == "ask" and opposite_best >= o.price)
        if crossed:
            return self._fill(o, ts, crossing_size, "phantom_cross")
        return None

    def cancel(self, o: RestingOrder) -> None:
        if o.status == "resting":
            o.status = "cancelled"
