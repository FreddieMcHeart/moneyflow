from datetime import date
from decimal import Decimal

from moneyflow.core.build import build
import pytest

from moneyflow.core.model import BuildRefused, LedgerEntry
from moneyflow.core.stages.arithmetic import arithmetic
from tests.core._builders import account, fx_rate, inputs, tx


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


def _fx_entry(
    id: str = "t1", day: int = 5, direction: str = "expense", amount: str = "1009.00", currency: str = "USD",
    account_id: str = "acc1", status: str = "kept",
) -> LedgerEntry:
    return LedgerEntry(
        transaction_id=id, part=0, date=date(2026, 1, day), account_id=account_id, category="food", subcategory=None,
        amount=Decimal(amount), currency=currency, status=status, reason=None, decided_by=None,  # type: ignore[arg-type]
        direction=direction, via=None,  # type: ignore[arg-type]
        budget_month=None, amount_uah=None, rate=None, rate_kind=None,
    )


def _inp(*rates):
    return inputs(
        accounts=(account("acc1", "USD"), account("fop", "USD", income_floor_step="100")),
        fx_rates=tuple(rates),
    )


RATE_5 = fx_rate(5, "41.4500", "official")
RATE_6 = fx_rate(6, "41.4537", "sell")


def test_fop_income_floors_to_100():
    (out,) = arithmetic([_fx_entry(direction="income", amount="6000.00", account_id="fop", day=6)], _inp(RATE_6))
    assert out.amount_uah == Decimal("248700.00")  # 6000 x 41.4537 = 248722.20
    assert (out.rate, out.rate_kind) == (Decimal("41.4537"), "sell")


def test_fop_expense_floors_to_5():
    (out,) = arithmetic([_fx_entry(account_id="fop", amount="1000.00", day=6)], _inp(RATE_6))
    assert out.amount_uah == Decimal("-41450.00")  # 41453.70 -> 41450, not 41400


def test_other_account_income_floors_to_5():
    (out,) = arithmetic([_fx_entry(direction="income", amount="1000.00", day=6)], _inp(RATE_6))
    assert out.amount_uah == Decimal("41450.00")


def test_reimbursement_uses_non_income_step():
    (out,) = arithmetic([_fx_entry(direction="reimbursement", account_id="fop", amount="1000.00", day=6)], _inp(RATE_6))
    assert out.amount_uah == Decimal("41450.00")


def test_floor_of_negative_amount_matches_old():
    # Old convert_rows floors the bank-positive magnitude: floor_to(1009.00 * 41.4500, 5) = 41820 (41823.05 -> 41820),
    # written positive. The signed ledger value is -41820; flooring the signed -41823.05 would give -41825.
    (out,) = arithmetic([_fx_entry(amount="1009.00")], _inp(RATE_5))
    assert out.amount == Decimal("-1009.00")
    assert out.amount_uah == Decimal("-41820.00")


def test_uah_entry_amount_uah_equals_amount():
    inp = inputs()
    out = arithmetic([_fx_entry(currency="UAH", amount="123.45"), _fx_entry(id="t2", currency="UAH", direction="income")], inp)
    assert [(e.amount, e.amount_uah, e.rate, e.rate_kind) for e in out] == [
        (Decimal("-123.45"), Decimal("-123.45"), None, None),
        (Decimal("1009.00"), Decimal("1009.00"), None, None),
    ]


def test_missing_rate_refuses_and_counts_all():
    entries = [_fx_entry(id="a", day=5), _fx_entry(id="b", day=9), _fx_entry(id="c", day=10)]
    with pytest.raises(BuildRefused) as refused:
        arithmetic(entries, _inp(RATE_5))
    assert "2 row(s)" in refused.value.reason
    assert refused.value.transaction_id == "b"


def test_neighbouring_day_rate_does_not_convert():
    with pytest.raises(BuildRefused):
        arithmetic([_fx_entry(day=5)], _inp(fx_rate(4, "41.0000"), fx_rate(6, "41.9000")))


@pytest.mark.parametrize("status", ["kept", "unresolved", "outside_cycle"])
def test_counted_statuses_are_converted(status):
    (out,) = arithmetic([_fx_entry(status=status)], _inp(RATE_5))
    assert out.amount_uah == Decimal("-41820.00")


def test_excluded_entry_is_not_converted_and_needs_no_rate():
    (out,) = arithmetic([_fx_entry(status="excluded")], _inp())
    assert (out.amount_uah, out.rate, out.rate_kind) == (None, None, None)


def test_unknown_currency_refuses():
    with pytest.raises(BuildRefused):
        arithmetic([_fx_entry(currency="EUR")], _inp())
