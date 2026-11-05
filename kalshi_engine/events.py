"""Normalised exchange events. Every price is a YES price in dollars (Decimal), on one book:
YES bids form the BID ladder; a NO bid at q is a YES ask at 1-q (ASK ladder)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Optional

Side = Literal["bid", "ask"]          # V2 semantics: bid = buy YES, ask = sell YES (= buy NO)


@dataclass(frozen=True)
class BookSnapshot:
    ticker: str
    ts: int                    # exchange time, ms
    bids: dict                 # {Decimal price: Decimal size}
    asks: dict
    seq: Optional[int] = None
    clock: Optional[int] = None
    recv_ns: Optional[int] = None


@dataclass(frozen=True)
class BookDelta:
    ticker: str
    ts: int
    side: Side                 # which ladder changed
    price: Decimal
    delta: Decimal             # signed change in resting size
    seq: Optional[int] = None
    clock: Optional[int] = None
    client_order_id: Optional[str] = None   # set on deltas caused by our own live orders
    recv_ns: Optional[int] = None


@dataclass(frozen=True)
class Trade:
    ticker: str
    ts: int
    price: Decimal             # YES price
    count: Decimal
    taker_side: Side           # taker_book_side: 'bid' = taker BOUGHT YES (hit asks);
                               #                  'ask' = taker SOLD YES  (hit bids)
    trade_id: Optional[str] = None
    clock: Optional[int] = None
    is_block: bool = False


@dataclass(frozen=True)
class Lifecycle:
    ticker: str
    ts: int
    kind: Literal["open", "pause", "resume", "close", "settle"]
    result: Optional[Literal["yes", "no"]] = None   # for settle


@dataclass(frozen=True)
class Gap:
    """Data-integrity break: seq gap, reconnect, missing data. Market is DIRTY until resync."""
    ticker: str
    ts: int
    reason: str


@dataclass(frozen=True)
class Tick:
    """Clock event for strategies that act on time (e.g. pull quotes at T-15min)."""
    ts: int
