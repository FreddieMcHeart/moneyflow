from datetime import date
from decimal import Decimal

from moneyflow.core.fx import rate_for, to_uah
from tests.core._builders import fx_rate

DAY = date(2026, 1, 5)


def test_sell_beats_official_same_day():
    rates = (fx_rate(5, "41.1000", "official"), fx_rate(5, "41.2500", "sell"))
    assert rate_for(DAY, rates) == (Decimal("41.2500"), "sell")


def test_sell_beats_official_whatever_the_order():
    rates = (fx_rate(5, "41.2500", "sell"), fx_rate(5, "41.1000", "official"))
    assert rate_for(DAY, rates) == (Decimal("41.2500"), "sell")


def test_official_used_when_no_sell():
    assert rate_for(DAY, (fx_rate(5, "41.1000", "official"),)) == (Decimal("41.1000"), "official")


def test_effective_and_midpoint_never_used():
    rates = (fx_rate(5, "44.0000", "effective"), fx_rate(5, "44.5000", "midpoint"))
    assert rate_for(DAY, rates) is None


def test_neighbouring_day_rate_not_used():
    rates = (fx_rate(4, "41.1000", "official"), fx_rate(6, "41.3000", "sell"))
    assert rate_for(DAY, rates) is None


def test_to_uah_floors_to_the_step():
    assert to_uah(Decimal("1009.00"), Decimal("41.4500"), Decimal("5")) == Decimal("41820.00")


def test_to_uah_keeps_an_exact_multiple():
    assert to_uah(Decimal("1000.00"), Decimal("41.8250"), Decimal("5")) == Decimal("41825.00")


def test_to_uah_floors_toward_minus_infinity():
    assert to_uah(Decimal("-1009.00"), Decimal("41.4500"), Decimal("5")) == Decimal("-41825.00")
