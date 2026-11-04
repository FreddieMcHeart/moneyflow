"""Budget cycles (A23, A24): income attributed to a month by hand decides where each cycle starts.

A cycle starts on the earliest anchor of its attributed month and ends the day before the next cycle starts;
the last cycle has no end. Derived from the entries on every call, never stored.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Ruling

_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class Cycle:
    budget_month: str
    start: date
    end: date | None  # None: the open cycle


def _income_account_ids(inputs: Inputs) -> frozenset[str]:
    return frozenset(a.id for a in inputs.accounts if a.role == "income")


def _anchor_dates(entries: list[LedgerEntry], inputs: Inputs) -> dict[str, date]:
    """Earliest date per candidate transaction: income on an income-role account that is still counted."""
    accounts = _income_account_ids(inputs)
    dates: dict[str, date] = {}
    for e in entries:
        if e.direction != "income" or e.account_id not in accounts or e.status not in ("kept", "unresolved"):
            continue
        dates[e.transaction_id] = min(e.date, dates.get(e.transaction_id, e.date))
    return dates


def _month_of(ruling: Ruling) -> str:
    month = ruling.payload.get("budget_month")
    if not isinstance(month, str) or not _MONTH.fullmatch(month):
        raise BuildRefused("attribute ruling needs a budget_month of the form YYYY-MM", ruling.transaction_id)
    return month


def _attribution(inputs: Inputs) -> dict[str, str]:
    return {r.transaction_id: _month_of(r) for r in inputs.rulings if r.kind == "attribute"}


def _anchors(entries: list[LedgerEntry], inputs: Inputs) -> dict[str, date]:
    """Budget month -> start date, refusing a candidate with no attribution and an attribution on a non-candidate."""
    candidates = _anchor_dates(entries, inputs)
    attribution = _attribution(inputs)
    for transaction_id in sorted(attribution):
        if transaction_id not in candidates:
            raise BuildRefused(f"attribute ruling {transaction_id} is not on an income-account income row", transaction_id)
    for transaction_id in sorted(candidates):
        if transaction_id not in attribution:
            raise BuildRefused(f"income {transaction_id} on an income account has no attribute ruling", transaction_id)
    starts: dict[str, date] = {}
    for transaction_id, day in candidates.items():
        month = attribution[transaction_id]
        starts[month] = min(day, starts.get(month, day))
    return starts


def build_cycles(entries: list[LedgerEntry], inputs: Inputs) -> tuple[Cycle, ...]:
    ordered = sorted(_anchors(entries, inputs).items(), key=lambda item: item[1])
    ends = [start - timedelta(days=1) for _, start in ordered[1:]] + [None]
    return tuple(Cycle(month, start, end) for (month, start), end in zip(ordered, ends))


def cycle_of(cycles: tuple[Cycle, ...], day: date) -> Cycle | None:
    for cycle in cycles:
        if day >= cycle.start and (cycle.end is None or day <= cycle.end):
            return cycle
    return None
