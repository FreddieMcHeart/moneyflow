from decimal import Decimal

import pytest

from moneyflow.core.build import build
from moneyflow.core.model import BuildRefused
from tests.core._builders import exclude_ruling, inputs, ruling, tx


def test_manual_expense_with_category_ruling_is_kept():
    manual = tx("manual-1", amount="40.00", source="manual")
    ledger = build(inputs(transactions=(manual,), rulings=(ruling("manual:manual-1", "manual-1", "food"),)))
    (entry,) = ledger
    assert (entry.status, entry.category, entry.amount, entry.direction) == ("kept", "food", Decimal("-40.00"), "expense")


def test_manual_income_is_kept_as_income():
    manual = tx("manual-2", amount="75.00", direction="income", source="manual")
    ledger = build(inputs(transactions=(manual,), rulings=(ruling("manual:manual-2", "manual-2", "misc"),)))
    (entry,) = ledger
    assert (entry.status, entry.amount, entry.direction) == ("kept", Decimal("75.00"), "income")


def test_manual_transaction_excluded_refuses():
    manual = tx("manual-3", source="manual")
    with pytest.raises(BuildRefused) as e:
        build(inputs(transactions=(manual,), rulings=(exclude_ruling("exclude:x:manual-3", "manual-3", "x"),)))
    assert e.value.reason == "manual transaction manual-3 did not end kept"
    assert e.value.transaction_id is None


def test_manual_transaction_left_unresolved_refuses():
    with pytest.raises(BuildRefused) as e:
        build(inputs(transactions=(tx("manual-4", source="manual"),)))
    assert "manual transaction manual-4 did not end kept" in e.value.reason


def test_bank_transaction_excluded_does_not_refuse():
    bank = tx("b1")
    ledger = build(inputs(transactions=(bank,), rulings=(exclude_ruling("exclude:x:b1", "b1", "x"),)))
    assert [e.status for e in ledger] == ["excluded"]
