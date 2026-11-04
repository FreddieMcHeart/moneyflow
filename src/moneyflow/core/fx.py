"""USD to UAH conversion (A35, A36): which rate applies to a day, and how a value is floored to the step."""

from datetime import date
from decimal import ROUND_FLOOR, Decimal

from moneyflow.core.model import FxRate

# The floor step for every row except a USD income row (N5); an income row's step is data on its account.
NON_INCOME_STEP = Decimal(5)

_CENTS = Decimal("0.01")


def rate_for(day: date, fx_rates: tuple[FxRate, ...]) -> tuple[Decimal, str] | None:
    """The rate for exactly this day: the first `sell` observation, else the first `official`, else None.

    `effective` and `midpoint` observations are never used for conversion, and a neighbouring day is never a fallback.
    """
    first: dict[str, Decimal] = {}
    for r in fx_rates:
        if r.date == day and r.kind in ("sell", "official"):
            first.setdefault(r.kind, r.rate)
    for kind in ("sell", "official"):
        if kind in first:
            return first[kind], kind
    return None


def to_uah(amount: Decimal, rate: Decimal, step: Decimal) -> Decimal:
    """`amount * rate` floored toward minus infinity to a multiple of `step`, to two decimal places."""
    # Decimal `//` truncates toward zero, so the floor is spelled out: it differs for a negative value.
    return ((amount * rate / step).to_integral_value(ROUND_FLOOR) * step).quantize(_CENTS)
