"""L2 order book for one market on the canonical YES-price book, with integrity tracking.

Rules (PAPER-ENGINE-SPEC §3): snapshot + deltas; per-subscription seq must be contiguous;
any gap marks the market DIRTY until the next snapshot. No strategy may trade a dirty book.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .events import BookDelta, BookSnapshot

ZERO = Decimal("0")


@dataclass
class Book:
    ticker: str
    bids: dict = field(default_factory=dict)   # price -> size
    asks: dict = field(default_factory=dict)
    last_seq: Optional[int] = None
    last_ts: int = 0
    dirty: bool = True                          # dirty until first snapshot
    dirty_reason: str = "no snapshot"

    # ------------------------------------------------------------------ updates
    def apply_snapshot(self, s: BookSnapshot) -> None:
        self.bids = {Decimal(p): Decimal(q) for p, q in s.bids.items() if Decimal(q) > 0}
        self.asks = {Decimal(p): Decimal(q) for p, q in s.asks.items() if Decimal(q) > 0}
        self.last_seq, self.last_ts = s.seq, s.ts
        self.dirty, self.dirty_reason = False, ""

    def apply_delta(self, d: BookDelta) -> None:
        if d.seq is not None and self.last_seq is not None and d.seq != self.last_seq + 1:
            self.mark_dirty(f"seq gap {self.last_seq}->{d.seq}")
        if d.seq is not None:
            self.last_seq = d.seq
        self.last_ts = d.ts
        ladder = self.bids if d.side == "bid" else self.asks
        new = ladder.get(d.price, ZERO) + d.delta
        if new < 0:
            self.mark_dirty(f"negative size at {d.price}")
            new = ZERO
        if new == 0:
            ladder.pop(d.price, None)
        else:
            ladder[d.price] = new

    def mark_dirty(self, reason: str) -> None:
        self.dirty, self.dirty_reason = True, reason

    # ------------------------------------------------------------------ queries
    def best_bid(self) -> Optional[Decimal]:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> Optional[Decimal]:
        return min(self.asks) if self.asks else None

    def mid(self) -> Optional[Decimal]:
        b, a = self.best_bid(), self.best_ask()
        return (a + b) / 2 if b is not None and a is not None else None

    def size_at(self, side: str, price: Decimal) -> Decimal:
        return (self.bids if side == "bid" else self.asks).get(price, ZERO)

    def walk(self, side: str, limit: Decimal, qty: Decimal):
        """Fills for a marketable order of `side` (bid = buy YES) up to `limit`, walking levels.
        Returns list of (price, qty). Never fills more than displayed depth (check 3)."""
        out, rem = [], qty
        if side == "bid":
            levels = sorted(p for p in self.asks if p <= limit)
            ladder = self.asks
        else:
            levels = sorted((p for p in self.bids if p >= limit), reverse=True)
            ladder = self.bids
        for p in levels:
            if rem <= 0:
                break
            take = min(rem, ladder[p])
            out.append((p, take))
            rem -= take
        return out
