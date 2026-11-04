from datetime import date
from decimal import Decimal

from moneyflow.core.build import build
from moneyflow.core.model import LedgerEntry
from moneyflow.core.stages.arithmetic import arithmetic
from tests.core._builders import inputs, tx


def test_expense_is_negative_income_positive():
    inp = inputs(
        transactions=(
            tx("e", amount="5.00", direction="expense"),
            tx("i", amount="7.00", direction="income"),
        )
    )
    by_id = {e.transaction_id: e.amount for e in build(inp)}
    assert by_id == {"e": Decimal("-5.00"), "i": Decimal("7.00")}


def _entry(direction: str, amount: str = "100.00") -> LedgerEntry:
    return LedgerEntry(
        transaction_id="t1", part=0, date=date(2026, 1, 1), account_id="acc1", category="food", subcategory=None,
        amount=Decimal(amount), currency="UAH", status="kept", reason=None, decided_by="rule:r1",
        direction=direction, via=None,
        budget_month=None, amount_uah=None, rate=None, rate_kind=None,  # type: ignore[arg-type]
    )


def test_reimbursement_entry_signs_positive():
    # The sign comes from the entry, not from the transaction: no transaction is supplied for it.
    (out,) = arithmetic([_entry("reimbursement")], inputs())
    assert out.amount == Decimal("100.00")


def test_expense_entry_signs_negative():
    (out,) = arithmetic([_entry("expense")], inputs())
    assert out.amount == Decimal("-100.00")
