"""Event-sourced ledger. Positions in YES contracts (negative = long NO). Decimal dollars.

Buying YES at p costs p per contract; selling YES at p (= buying NO at 1-p) costs (1-p) per
contract of collateral. Settlement pays 1 per YES contract on 'yes', 1 per NO contract on 'no'.
P&L is computed as cash change + settlement, so it is exact for a fully collateralised venue.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0")


@dataclass
class Fill:
    ts: int
    ticker: str
    side: str          # 'bid' bought YES | 'ask' sold YES
    price: Decimal
    qty: Decimal
    fee: Decimal
    is_taker: bool
    tag: str = ""
    cause: str = ""
    oid: str = ""


@dataclass
class Ledger:
    cash: Decimal = ZERO
    fills: list = field(default_factory=list)
    yes: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))   # ticker -> YES qty held
    no: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))    # ticker -> NO qty held
    settled: dict = field(default_factory=dict)                            # ticker -> result
    fees: Decimal = ZERO

    def record(self, f: Fill) -> None:
        self.fills.append(f)
        self.fees += f.fee
        self.cash -= f.fee
        if f.side == "bid":                       # buy YES; net against NO held first
            close = min(self.no[f.ticker], f.qty)
            self.no[f.ticker] -= close           # closing NO: receive (1-p) back... modelled as
            self.cash += close * (1 - f.price) - close * ZERO   # selling NO at 1-p
            self.cash -= (f.qty - close) * f.price
            self.yes[f.ticker] += f.qty - close
        else:                                     # sell YES = buy NO at 1-p; net against YES held
            close = min(self.yes[f.ticker], f.qty)
            self.yes[f.ticker] -= close
            self.cash += close * f.price
            self.cash -= (f.qty - close) * (1 - f.price)
            self.no[f.ticker] += f.qty - close

    def settle(self, ticker: str, result: str) -> Decimal:
        if ticker in self.settled:
            return ZERO
        pay = self.yes[ticker] if result == "yes" else self.no[ticker]
        self.cash += pay
        self.settled[ticker] = result
        self.yes[ticker] = ZERO
        self.no[ticker] = ZERO
        return pay

    def position(self, ticker: str) -> Decimal:
        return self.yes[ticker] - self.no[ticker]

    def check_invariants(self) -> None:
        for f in self.fills:
            assert ZERO < f.price < 1, f
            assert f.fee >= 0, f
            assert f.qty > 0, f
