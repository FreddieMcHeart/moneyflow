import pytest

from datetime import date
from decimal import Decimal

from moneyflow.core.model import BuildRefused, LedgerEntry
from moneyflow.core.reports import category_totals


def entry(
    amount: str,
    day: int = 5,
    category: str | None = "Cafe",
    subcategory: str | None = None,
    currency: str = "UAH",
    status: str = "kept",
    tid: str = "t",
) -> LedgerEntry:
    return LedgerEntry(
        transaction_id=tid,
        part=0,
        date=date(2026, 1, day),
        account_id="acc1",
        category=category,
        subcategory=subcategory,
        amount=Decimal(amount),
        currency=currency,
        status=status,  # type: ignore[arg-type]
        reason=None,
        decided_by=None,
        direction="expense",
        via=None,
    )


FROM, TO = date(2026, 1, 1), date(2026, 1, 31)


def test_totals_sum_kept_only():
    ledger = (
        entry("-10.00", tid="a"),
        entry("-5.50", tid="b"),
        entry("-99.00", status="excluded", tid="c"),
        entry("-77.00", status="unresolved", category=None, tid="d"),
    )
    assert category_totals(ledger, FROM, TO) == {("Cafe", None, "UAH"): Decimal("-15.50")}


def test_dates_inclusive_both_ends():
    ledger = (
        entry("-1.00", day=1, tid="a"),
        entry("-2.00", day=10, tid="b"),
        entry("-4.00", day=31, tid="c"),
    )
    assert category_totals(ledger, date(2026, 1, 1), date(2026, 1, 31)) == {("Cafe", None, "UAH"): Decimal("-7.00")}
    assert category_totals(ledger, date(2026, 1, 2), date(2026, 1, 30)) == {("Cafe", None, "UAH"): Decimal("-2.00")}
    assert category_totals(ledger, date(2026, 1, 1), date(2026, 1, 9)) == {("Cafe", None, "UAH"): Decimal("-1.00")}
    assert category_totals(ledger, date(2026, 1, 11), date(2026, 1, 31)) == {("Cafe", None, "UAH"): Decimal("-4.00")}


def test_currency_kept_apart():
    ledger = (entry("-10.00", tid="a"), entry("-3.00", currency="USD", tid="b"))
    assert category_totals(ledger, FROM, TO) == {
        ("Cafe", None, "UAH"): Decimal("-10.00"),
        ("Cafe", None, "USD"): Decimal("-3.00"),
    }


def test_subcategory_none_is_its_own_key():
    ledger = (entry("-1.00", tid="a"), entry("-2.00", subcategory="Coffee", tid="b"))
    assert category_totals(ledger, FROM, TO) == {
        ("Cafe", None, "UAH"): Decimal("-1.00"),
        ("Cafe", "Coffee", "UAH"): Decimal("-2.00"),
    }


def test_empty_ledger_gives_empty_totals():
    assert category_totals((), FROM, TO) == {}


def test_kept_entry_without_category_refuses():
    with pytest.raises(BuildRefused) as e:
        category_totals((entry("-1.00", category=None, tid="bad"),), FROM, TO)
    assert e.value.transaction_id == "bad"
