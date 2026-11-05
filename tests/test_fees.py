from decimal import Decimal as D
import pytest
from kalshi_engine.fees import fill_fee, raw_fee, FeeAccumulator, SeriesFees


def test_taker_golden_table():
    # fee schedule examples (effective 2026-07-07), direct-member centi-cent rounding
    assert fill_fee("0.80", 100, True) == D("1.1200")      # float ceil would give 1.13
    assert fill_fee("0.50", 1, True) == D("0.0175")
    assert fill_fee("0.95", 100, True) == D("0.3325")
    assert fill_fee("0.95", 100, True, account="fcm") == D("0.34")   # matches PDF whole-cent table
    assert fill_fee("0.50", 1, True, account="fcm") == D("0.02")


def test_maker_default_zero_and_multiplier():
    assert fill_fee("0.50", 100, False) == D("0")
    assert fill_fee("0.50", 100, False, SeriesFees(D(1), D(1))) == D("0.4375")
    assert fill_fee("0.50", 10, True, SeriesFees(D(2), D(1))) == D("0.3500")   # KXMVE taker x2


def test_symmetry_and_bounds():
    for c in range(1, 100):
        p = D(c) / 100
        assert raw_fee(p, 10, True) == raw_fee(1 - p, 10, True)
    with pytest.raises(ValueError):
        raw_fee("1.00", 1, True)


def test_accumulator_matches_single_order_total():
    acc = FeeAccumulator()
    total = sum(acc.charge("0.37", D("0.33"), True) for _ in range(30))
    assert total == fill_fee("0.37", D("9.9"), True)
