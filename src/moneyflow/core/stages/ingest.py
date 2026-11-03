"""Ingest: one entry per transaction, nothing decided yet."""

from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Transaction
from moneyflow.core.owners import budget_owner


def _entry(tx: Transaction, include: bool) -> LedgerEntry:
    return LedgerEntry(
        transaction_id=tx.id,
        part=0,
        date=tx.date,
        account_id=tx.account_id,
        category=None,
        subcategory=None,
        amount=tx.amount,
        currency=tx.currency,
        status="unresolved" if include else "excluded",
        reason=None if include else "account-not-included",
        decided_by=None,
    )


def _refuse_uncategorised_white_card(inputs: Inputs) -> None:
    """A4: a white-card leg is kept on purpose, so it must carry its category on purpose too."""
    categorised = {r.transaction_id for r in inputs.rulings if r.kind == "category"}
    for ruling in inputs.rulings:
        if ruling.kind == "white_card" and ruling.transaction_id not in categorised:
            raise BuildRefused("white-card leg without a category ruling", ruling.transaction_id)


def ingest(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    budget_owner(inputs)  # refuses unless exactly one owner carries the budget
    _refuse_uncategorised_white_card(inputs)
    accounts = {a.id: a for a in inputs.accounts}
    seen: set[str] = set()
    out: list[LedgerEntry] = []
    for tx in inputs.transactions:
        account = accounts.get(tx.account_id)
        if account is None:
            raise BuildRefused(f"unknown account {tx.account_id}", tx.id)
        if tx.id in seen:
            raise BuildRefused("duplicate transaction id", tx.id)
        if tx.currency != account.currency:
            raise BuildRefused(f"currency {tx.currency} differs from account currency {account.currency}", tx.id)
        seen.add(tx.id)
        out.append(_entry(tx, account.include))
    return out
