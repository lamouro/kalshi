"""Strategy interface — identical in replay, shadow and live (only the gateway changes).

A strategy is pure: on_event(event, ctx) -> list[Command]. No I/O, no wall-clock reads.
ctx exposes read-only views supplied by the runner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Place:
    ticker: str
    side: str            # 'bid' buy YES | 'ask' sell YES (= buy NO)
    price: Decimal
    size: Decimal
    post_only: bool = True
    tag: str = ""        # strategy label, for attribution in combinations
    coid: str = ""       # client order id (idempotency)


@dataclass(frozen=True)
class Cancel:
    oid: str


@dataclass(frozen=True)
class CancelAll:
    ticker: Optional[str] = None
    tag: Optional[str] = None


@dataclass
class Ctx:
    now: int = 0
    quotes: dict = field(default_factory=dict)        # ticker -> (bid, ask) best, Decimal or None
    open_orders: dict = field(default_factory=dict)   # oid -> RestingOrder
    positions: dict = field(default_factory=dict)     # ticker -> signed YES contracts
    meta: dict = field(default_factory=dict)          # ticker -> market metadata dict
    cash: Decimal = Decimal("0")


class Strategy:
    name = "base"

    def on_event(self, evt, ctx: Ctx) -> list:
        return []
