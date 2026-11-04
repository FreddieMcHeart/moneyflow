from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from moneyflow.core.cycles import Cycle
from moneyflow.core.model import Account, BuildRefused, LedgerEntry
from moneyflow.core.reports import cash_in_hand, category_totals, month_summaries
from tests.core._builders import inputs


def entry(
    amount: str,
    day: int = 5,
    category: str | None = "Cafe",
    subcategory: str | None = None,
    currency: str = "UAH",
    status: str = "kept",
    tid: str = "t",
    budget_month: str | None = None,
    direction: str = "expense",
    reason: str | None = None,
    account_id: str = "acc1",
    month: int = 1,
) -> LedgerEntry:
    return LedgerEntry(
        transaction_id=tid,
        part=0,
        date=date(2026, month, day),
        account_id=account_id,
        category=category,
        subcategory=subcategory,
        amount=Decimal(amount),
        currency=currency,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        decided_by=None,
        direction=direction,  # type: ignore[arg-type]
        via=None,
        budget_month=budget_month,
        amount_uah=None,
        rate=None,
        rate_kind=None,
    )


FROM, TO = date(2026, 1, 1), date(2026, 1, 31)


def test_totals_sum_kept_only():
    ledger = (
        entry("-10.00", tid="a"),
        entry("-5.50", tid="b"),
        entry("-99.00", status="excluded", tid="c"),
        entry("-77.00", status="unresolved", category=None, tid="d"),
    )
    assert category_totals(ledger, start=FROM, end=TO) == {("Cafe", None, "UAH"): Decimal("-15.50")}


def test_dates_inclusive_both_ends():
    ledger = (
        entry("-1.00", day=1, tid="a"),
        entry("-2.00", day=10, tid="b"),
        entry("-4.00", day=31, tid="c"),
    )
    assert category_totals(ledger, start=date(2026, 1, 1), end=date(2026, 1, 31)) == {("Cafe", None, "UAH"): Decimal("-7.00")}
    assert category_totals(ledger, start=date(2026, 1, 2), end=date(2026, 1, 30)) == {("Cafe", None, "UAH"): Decimal("-2.00")}
    assert category_totals(ledger, start=date(2026, 1, 1), end=date(2026, 1, 9)) == {("Cafe", None, "UAH"): Decimal("-1.00")}
    assert category_totals(ledger, start=date(2026, 1, 11), end=date(2026, 1, 31)) == {("Cafe", None, "UAH"): Decimal("-4.00")}


def test_currency_kept_apart():
    ledger = (entry("-10.00", tid="a"), entry("-3.00", currency="USD", tid="b"))
    assert category_totals(ledger, start=FROM, end=TO) == {
        ("Cafe", None, "UAH"): Decimal("-10.00"),
        ("Cafe", None, "USD"): Decimal("-3.00"),
    }


def test_subcategory_none_is_its_own_key():
    ledger = (entry("-1.00", tid="a"), entry("-2.00", subcategory="Coffee", tid="b"))
    assert category_totals(ledger, start=FROM, end=TO) == {
        ("Cafe", None, "UAH"): Decimal("-1.00"),
        ("Cafe", "Coffee", "UAH"): Decimal("-2.00"),
    }


def test_empty_ledger_gives_empty_totals():
    assert category_totals((), start=FROM, end=TO) == {}


def test_kept_entry_without_category_refuses():
    with pytest.raises(BuildRefused) as e:
        category_totals((entry("-1.00", category=None, tid="bad"),), start=FROM, end=TO)
    assert e.value.transaction_id == "bad"


JAN = Cycle("2026-01", date(2026, 1, 1), date(2026, 1, 31))
FEB_OPEN = Cycle("2026-02", date(2026, 2, 1), None)


def _inp(today: date = date(2026, 2, 10), accounts=None):
    return replace(inputs(accounts=accounts or ()), today=today)


def test_income_and_total_actual_per_currency():
    ledger = (
        entry("100.00", direction="income", budget_month="2026-01", tid="a"),
        entry("10.00", direction="income", currency="USD", budget_month="2026-01", tid="b"),
        entry("-30.00", budget_month="2026-01", tid="c"),
        entry("-2.00", currency="USD", budget_month="2026-01", tid="d"),
        entry("-5.00", budget_month="2026-02", tid="e"),
    )
    (jan, feb) = month_summaries(ledger, (JAN, FEB_OPEN), _inp())
    assert dict(jan.income) == {"UAH": Decimal("100.00"), "USD": Decimal("10.00")}
    assert dict(jan.total_actual) == {"UAH": Decimal("-30.00"), "USD": Decimal("-2.00")}
    assert dict(feb.income) == {} and dict(feb.total_actual) == {"UAH": Decimal("-5.00")}


def test_total_actual_includes_uncategorised():
    ledger = (entry("-7.00", status="unresolved", category=None, budget_month="2026-01"),)
    (jan,) = month_summaries(ledger, (JAN,), _inp())
    assert dict(jan.total_actual) == {"UAH": Decimal("-7.00")}


def test_total_actual_excludes_outside_cycle():
    ledger = (
        entry("-7.00", status="outside_cycle", category=None, tid="a"),
        entry("-9.00", status="excluded", budget_month="2026-01", tid="b"),
        entry("-1.00", budget_month="2026-01", tid="c"),
    )
    (jan,) = month_summaries(ledger, (JAN,), _inp())
    assert dict(jan.total_actual) == {"UAH": Decimal("-1.00")}


def test_open_cycle_days_total_is_previous_length():
    (jan, feb) = month_summaries((), (JAN, FEB_OPEN), _inp())
    assert (jan.days_total, feb.days_total, feb.cycle_open, jan.cycle_open) == (31, 31, True, False)
    (only,) = month_summaries((), (FEB_OPEN,), _inp())
    assert (only.days_total, only.days_left) == (None, None)


def test_days_left_from_today():
    assert month_summaries((), (JAN, FEB_OPEN), _inp(today=date(2026, 2, 10)))[1].days_left == 21
    assert month_summaries((), (JAN, FEB_OPEN), _inp(today=date(2026, 2, 1)))[1].days_left == 30
    assert month_summaries((), (JAN, FEB_OPEN), _inp(today=date(2026, 4, 1)))[1].days_left == 0


def test_days_left_never_exceeds_total_when_today_precedes_the_cycle():
    assert month_summaries((), (JAN, FEB_OPEN), _inp(today=date(2026, 1, 25)))[1].days_left == 30


def test_closed_cycle_has_no_days_left():
    assert month_summaries((), (JAN, FEB_OPEN), _inp())[0].days_left == 0


def test_zero_length_closed_cycle_refuses():
    with pytest.raises(BuildRefused, match="2026-01"):
        month_summaries((), (Cycle("2026-01", date(2026, 1, 5), date(2026, 1, 4)),), _inp())


CUTOVER = date(2026, 1, 10)
CASH = Account(id="cash", name="cash", currency="UAH", owner="o", role="main", include=True, cash_from=CUTOVER,
               fx_income_floor_step=Decimal("5"), cash_opening=Decimal("100.00"))
CARD = Account(id="acc1", name="acc1", currency="UAH", owner="o", role="main", include=True, cash_from=None,
               fx_income_floor_step=Decimal("5"), cash_opening=None)


def test_cash_in_hand_before_cutover_is_none():
    early = Cycle("2025-12", date(2025, 12, 1), date(2026, 1, 9))
    assert cash_in_hand(early, (), _inp(accounts=(CARD, CASH))) is None
    assert cash_in_hand(JAN, (), _inp(accounts=(CARD,))) is None


def test_cash_in_hand_on_the_cutover_day_is_the_opening_not_none():
    on_cutover = Cycle("2025-12", date(2025, 12, 1), CUTOVER)
    assert cash_in_hand(on_cutover, (), _inp(accounts=(CARD, CASH))) == Decimal("100.00")


def test_cash_in_hand_adds_withdrawals_subtracts_deposits_and_spending():
    ledger = (
        entry("-50.00", status="excluded", reason="cash-withdrawal", day=12, tid="w"),
        entry("-20.00", status="excluded", reason="cash-deposit", day=13, tid="d1"),
        entry("10.00", status="excluded", reason="cash-deposit", day=14, direction="income", tid="d2"),
        entry("-30.00", day=15, account_id="cash", tid="s"),
        entry("-999.00", day=15, tid="other"),
        entry("-500.00", status="excluded", reason="cash-withdrawal", day=9, tid="early"),
        entry("-40.00", status="excluded", reason="internal-transfer", day=16, tid="skip"),
    )
    assert cash_in_hand(JAN, ledger, _inp(accounts=(CARD, CASH))) == Decimal("90.00")


def test_cash_in_hand_open_cycle_stops_at_today():
    ledger = (
        entry("-30.00", day=14, month=1, account_id="cash", tid="in"),
        entry("-5.00", day=20, month=1, account_id="cash", tid="late"),
    )
    open_jan = Cycle("2026-01", date(2026, 1, 1), None)
    assert cash_in_hand(open_jan, ledger, _inp(today=date(2026, 1, 15), accounts=(CARD, CASH))) == Decimal("70.00")
    assert cash_in_hand(open_jan, ledger, _inp(today=date(2026, 1, 20), accounts=(CARD, CASH))) == Decimal("65.00")


def test_month_summary_carries_cash_in_hand():
    (jan,) = month_summaries((entry("-30.00", day=15, account_id="cash"),), (JAN,), _inp(accounts=(CARD, CASH)))
    assert jan.cash_in_hand == Decimal("70.00")


def test_category_totals_by_budget_month():
    ledger = (
        entry("-1.00", budget_month="2026-01", tid="a", day=1),
        entry("-2.00", budget_month="2026-02", tid="b", day=1),
        entry("-4.00", budget_month="2026-01", tid="c", day=31),
        entry("-8.00", status="outside_cycle", tid="d"),
    )
    assert category_totals(ledger, budget_month="2026-01") == {("Cafe", None, "UAH"): Decimal("-5.00")}
    assert category_totals(ledger, budget_month="2026-03") == {}


def test_category_totals_refuses_month_with_dates_or_nothing():
    with pytest.raises(BuildRefused):
        category_totals((), budget_month="2026-01", start=FROM, end=TO)
    with pytest.raises(BuildRefused):
        category_totals(())
    with pytest.raises(BuildRefused):
        category_totals((), start=FROM)
