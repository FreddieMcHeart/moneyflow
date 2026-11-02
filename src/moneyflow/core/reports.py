"""Read-side reports over a built ledger."""

from datetime import date
from decimal import Decimal

from moneyflow.core.model import BuildRefused, Ledger


def category_totals(ledger: Ledger, date_from: date, date_to: date) -> dict[tuple[str, str | None, str], Decimal]:
    """Sum signed kept amounts per (category, subcategory, currency); both dates inclusive."""
    totals: dict[tuple[str, str | None, str], Decimal] = {}
    for e in ledger:
        if e.status != "kept" or not date_from <= e.date <= date_to:
            continue
        if e.category is None:
            raise BuildRefused("kept entry without a category", e.transaction_id)
        key = (e.category, e.subcategory, e.currency)
        totals[key] = totals.get(key, Decimal(0)) + e.amount
    return totals
