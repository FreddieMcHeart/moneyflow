"""Arithmetic: the sign and the UAH value. Expense is negative; income and reimbursement stay positive.

A USD entry converts at the rate of its own day: an income row at the `official` rate only, any other row at `sell`, else `official`. As in the old `convert_rows`, the bank-positive magnitude is floored
(to the account's `fx_income_floor_step` for an income row, otherwise to `NON_INCOME_STEP`) and the sign is applied after,
so an expense of 1009.00 at 41.45 is -41820, never -41825. Entries that are not counted (excluded) are not converted.
"""

from dataclasses import replace
from decimal import Decimal

from moneyflow.core.fx import NON_INCOME_STEP, official_rate_for, rate_for, to_uah
from moneyflow.core.model import BuildRefused, FxRate, Inputs, LedgerEntry

_CONVERTED = ("kept", "unresolved", "outside_cycle")


def _step(e: LedgerEntry, income_steps: dict[str, Decimal]) -> Decimal:
    return income_steps[e.account_id] if e.direction == "income" else NON_INCOME_STEP


def _sign(e: LedgerEntry) -> int:
    return -1 if e.direction == "expense" else 1


def _convert(e: LedgerEntry, rate: tuple[Decimal, str] | None, income_steps: dict[str, Decimal]) -> LedgerEntry:
    signed = _sign(e) * e.amount
    if e.currency == "UAH":
        return replace(e, amount=signed, amount_uah=signed, rate=None, rate_kind=None)
    assert rate is not None
    return replace(
        e, amount=signed, amount_uah=_sign(e) * to_uah(e.amount, rate[0], _step(e, income_steps)), rate=rate[0], rate_kind=rate[1]
    )


def _rate(e: LedgerEntry, fx_rates: tuple[FxRate, ...]) -> tuple[Decimal, str] | None:
    """An income row converts at the NBU `official` rate only; every other row at `sell`, else `official`."""
    return official_rate_for(e.date, fx_rates) if e.direction == "income" else rate_for(e.date, fx_rates)


def _rates(entries: list[LedgerEntry], inputs: Inputs) -> list[tuple[Decimal, str] | None]:
    """The rate per entry (None for a UAH or an uncounted entry); refuses every entry the rule cannot convert."""
    found: list[tuple[Decimal, str] | None] = []
    missing: list[str] = []
    missing_income: list[str] = []
    for e in entries:
        if e.status not in _CONVERTED or e.currency == "UAH":
            found.append(None)
            continue
        if e.currency != "USD":
            raise BuildRefused(f"currency {e.currency} has no conversion rule", e.transaction_id)
        hit = _rate(e, inputs.fx_rates)
        if hit is None:
            (missing_income if e.direction == "income" else missing).append(e.transaction_id)
        found.append(hit)
    if missing_income:
        raise BuildRefused(f"no official USD rate for {len(missing_income)} income row(s)", missing_income[0])
    if missing:
        raise BuildRefused(f"no sell or official USD rate for {len(missing)} row(s)", missing[0])
    return found


def arithmetic(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    income_steps = {a.id: a.fx_income_floor_step for a in inputs.accounts}
    rates = _rates(entries, inputs)
    return [
        _convert(e, rate, income_steps) if e.status in _CONVERTED else replace(e, amount=_sign(e) * e.amount)
        for e, rate in zip(entries, rates)
    ]
