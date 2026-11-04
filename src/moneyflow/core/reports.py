"""Read-side reports over a built ledger: category totals and the per-cycle month summary (A38, A40)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from moneyflow.core.cash import WITHDRAWAL
from moneyflow.core.cycles import Cycle
from moneyflow.core.model import Account, BuildRefused, Inputs, Ledger, LedgerEntry

DEPOSIT = "cash-deposit"
_COUNTED = ("kept", "unresolved")


@dataclass(frozen=True)
class MonthSummary:
    budget_month: str
    cycle_start: date
    cycle_end: date | None
    cycle_open: bool
    days_total: int | None
    days_left: int | None
    income: Mapping[str, Decimal]  # per currency, original-currency amount, never folded into one number
    total_actual: Mapping[str, Decimal]  # per currency, every non-income counted row, uncategorised included
    cash_in_hand: Decimal | None


def _in_scope(e: LedgerEntry, budget_month: str | None, start: date | None, end: date | None) -> bool:
    if e.status != "kept":
        return False
    if budget_month is not None:
        return e.budget_month == budget_month
    return start is not None and end is not None and start <= e.date <= end


def _check_scope(budget_month: str | None, start: date | None, end: date | None) -> None:
    if budget_month is not None and (start is not None or end is not None):
        raise BuildRefused("category totals take a budget month or a date range, not both", None)
    if budget_month is None and (start is None or end is None):
        raise BuildRefused("category totals need a budget month or both dates", None)


def category_totals(
    ledger: Ledger, *, budget_month: str | None = None, start: date | None = None, end: date | None = None
) -> dict[tuple[str, str | None, str], Decimal]:
    """Sum signed kept amounts per (category, subcategory, currency), by budget month or by date range (inclusive)."""
    _check_scope(budget_month, start, end)
    totals: dict[tuple[str, str | None, str], Decimal] = {}
    for e in ledger:
        if not _in_scope(e, budget_month, start, end):
            continue
        if e.category is None:
            raise BuildRefused("kept entry without a category", e.transaction_id)
        key = (e.category, e.subcategory, e.currency)
        totals[key] = totals.get(key, Decimal(0)) + e.amount
    return totals


def _length(cycle: Cycle) -> int | None:
    """Inclusive day count of a closed cycle; None while open. A length of zero or less is a data error."""
    if cycle.end is None:
        return None
    length = (cycle.end - cycle.start).days + 1
    if length <= 0:
        raise BuildRefused(f"cycle {cycle.budget_month} runs {cycle.start}..{cycle.end}, a length of {length} days", None)
    return length


def _elapsed(cycle: Cycle, today: date) -> int:
    return max(1, (today - cycle.start).days + 1)


def _days(cycles: tuple[Cycle, ...], i: int, today: date) -> tuple[int | None, int | None]:
    """(days_total, days_left): closed cycle, its length and 0; open cycle, the previous length minus elapsed days."""
    closed = _length(cycles[i])
    if closed is not None:
        return closed, 0
    total = _length(cycles[i - 1]) if i > 0 else None
    return total, None if total is None else max(0, total - _elapsed(cycles[i], today))


def _sums(ledger: Ledger) -> tuple[dict[str, dict[str, Decimal]], dict[str, dict[str, Decimal]]]:
    """(income, total_actual): budget month -> currency -> amount, over every kept or unresolved row in a cycle."""
    income: dict[str, dict[str, Decimal]] = {}
    actual: dict[str, dict[str, Decimal]] = {}
    for e in ledger:
        if e.status not in _COUNTED or e.budget_month is None:
            continue
        bucket = income if e.direction == "income" else actual
        per_currency = bucket.setdefault(e.budget_month, {})
        per_currency[e.currency] = per_currency.get(e.currency, Decimal(0)) + e.amount
    return income, actual


def _cash_account(inputs: Inputs) -> Account | None:
    found = [a for a in inputs.accounts if a.cash_from is not None]
    if len(found) > 1:
        raise BuildRefused("more than one cash account", None)
    return found[0] if found else None


def _movement(e: LedgerEntry) -> Decimal:
    """What an excluded cash row does to cash in hand: the rule decides the sign, never the row's direction."""
    if e.status == "excluded" and e.reason == WITHDRAWAL:
        return abs(e.amount)
    if e.status == "excluded" and e.reason == DEPOSIT:
        return -abs(e.amount)
    return Decimal(0)


def _cash_change(e: LedgerEntry, account: Account) -> Decimal:
    """One entry's effect on cash in hand: an excluded withdrawal or deposit, or signed spending on the cash account."""
    counted = e.status in ("kept", "unresolved") and e.budget_month is not None
    spend = e.amount if counted and e.account_id == account.id else Decimal(0)
    return _movement(e) + spend


def cash_in_hand(cycle: Cycle, ledger: Ledger, inputs: Inputs) -> Decimal | None:
    """Cash at the cycle's end (today for the open cycle): opening + withdrawals - deposits + signed cash spending.

    None when there is no cash account, or when the cycle ended before the cutover; never 0 for those.
    """
    account = _cash_account(inputs)
    if account is None or account.cash_from is None:
        return None
    if account.cash_opening is None:
        raise BuildRefused(f"cash account {account.id} has no opening balance", None)
    end = cycle.end if cycle.end is not None else inputs.today
    if end < account.cash_from:
        return None
    return account.cash_opening + sum(
        (_cash_change(e, account) for e in ledger if account.cash_from <= e.date <= end), Decimal(0)
    )


def month_summaries(ledger: Ledger, cycles: tuple[Cycle, ...], inputs: Inputs) -> tuple[MonthSummary, ...]:
    """One summary per cycle, in cycle order. Balance, Safe per day and Total money UAH are not built (N3)."""
    income, actual = _sums(ledger)
    out = []
    for i, c in enumerate(cycles):
        total, left = _days(cycles, i, inputs.today)
        out.append(MonthSummary(
            budget_month=c.budget_month, cycle_start=c.start, cycle_end=c.end, cycle_open=c.end is None,
            days_total=total, days_left=left, income=income.get(c.budget_month, {}),
            total_actual=actual.get(c.budget_month, {}), cash_in_hand=cash_in_hand(c, ledger, inputs),
        ))
    return tuple(out)
