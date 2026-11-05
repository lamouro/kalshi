"""Kalshi fee model (schedule effective 2026-07-07; direct-member rounding since 2026-05-28).

fee = round_up(rate * M * C * P * (1 - P)), with
  rate = 0.07 (taker) or 0.0175 (maker);
  M    = the series multiplier for that side (maker M defaults to 0 = no maker fee);
  C    = contracts (fractional, 0.01 granularity);
  P    = price in dollars.
Rounding:
  * the model fee rounds UP to $0.000001;
  * direct-member balances align to $0.0001: an order's accumulated fee is rounded up to the
    next $0.0001, tracked with a per-order accumulator so the sum over fills matches the
    exchange (FeeAccumulator);
  * FCM/broker accounts round each fill up to the whole cent (legacy behaviour; option).
All arithmetic is Decimal. Never float: ceil(0.07*100*0.8*0.2) in float gives 113c, true is 112c.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

TAKER_RATE = Decimal("0.07")
MAKER_RATE = Decimal("0.0175")
MICRO = Decimal("0.000001")
CENTICENT = Decimal("0.0001")
CENT = Decimal("0.01")


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class SeriesFees:
    """Per-series fee parameters, as read from /series (fee_type, fee_multiplier)."""
    taker_mult: Decimal = Decimal("1")
    maker_mult: Decimal = Decimal("0")

    @classmethod
    def from_api(cls, fee_type: str, fee_multiplier) -> "SeriesFees":
        m = D(fee_multiplier if fee_multiplier is not None else 1)
        if fee_type in ("quadratic_with_maker_fees",):
            return cls(taker_mult=m, maker_mult=m)
        if fee_type in ("flat", "zero") or m == 0:
            return cls(taker_mult=m, maker_mult=Decimal("0"))
        return cls(taker_mult=m, maker_mult=Decimal("0"))


def raw_fee(price, contracts, is_taker: bool, fees: SeriesFees = SeriesFees()) -> Decimal:
    p, c = D(price), D(contracts)
    if not (Decimal("0") < p < Decimal("1")):
        raise ValueError(f"price {p} outside (0,1)")
    rate, mult = (TAKER_RATE, fees.taker_mult) if is_taker else (MAKER_RATE, fees.maker_mult)
    f = rate * mult * c * p * (1 - p)
    return f.quantize(MICRO, rounding=ROUND_CEILING)


def fill_fee(price, contracts, is_taker: bool, fees: SeriesFees = SeriesFees(),
             account: str = "direct") -> Decimal:
    """Fee charged for a single fill viewed on its own (one-fill order)."""
    f = raw_fee(price, contracts, is_taker, fees)
    q = CENTICENT if account == "direct" else CENT
    return f.quantize(q, rounding=ROUND_CEILING)


@dataclass
class FeeAccumulator:
    """Per-order accumulator: charged-so-far always equals round_up(sum of raw fees)."""
    account: str = "direct"
    raw_total: Decimal = Decimal("0")
    charged_total: Decimal = Decimal("0")

    def charge(self, price, contracts, is_taker: bool, fees: SeriesFees = SeriesFees()) -> Decimal:
        self.raw_total += raw_fee(price, contracts, is_taker, fees)
        q = CENTICENT if self.account == "direct" else CENT
        target = self.raw_total.quantize(q, rounding=ROUND_CEILING)
        inc = target - self.charged_total
        self.charged_total = target
        return inc
