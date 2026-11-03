"""The split kind: how one `split:<tx>:<n>` ruling becomes one part of a transaction."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from moneyflow.core.kinds._fields import _text
from moneyflow.core.model import BuildRefused, Ruling


@dataclass(frozen=True)
class SplitPart:
    n: int  # 1-based part number, read from the ruling id
    amount: Decimal  # positive, like the bank amount
    category: str
    subcategory: str | None
    date: date | None
    ruling_id: str


def _number(ruling: Ruling) -> int:
    tail = ruling.id.rsplit(":", 1)[-1]
    if not tail.isdigit():
        raise BuildRefused(f"split ruling {ruling.id}: id does not end in a part number", ruling.transaction_id)
    return int(tail)


def _amount(ruling: Ruling) -> Decimal:
    where = f"split ruling {ruling.id}"
    try:
        amount = Decimal(_text(ruling.payload, "amount", where, ruling.transaction_id))
    except ArithmeticError as broken:
        raise BuildRefused(f"{where}: amount is not a number", ruling.transaction_id) from broken
    if not amount.is_finite() or amount <= 0:
        raise BuildRefused(f"{where}: amount must be positive", ruling.transaction_id)
    return amount


def _day(ruling: Ruling) -> date | None:
    value = ruling.payload.get("date")
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as broken:
        raise BuildRefused(f"split ruling {ruling.id}: date is not a date", ruling.transaction_id) from broken


def apply_ruling(ruling: Ruling) -> SplitPart:
    category = _text(ruling.payload, "category", f"split ruling {ruling.id}", ruling.transaction_id)
    return SplitPart(_number(ruling), _amount(ruling), category, ruling.payload.get("subcategory"), _day(ruling), ruling.id)
