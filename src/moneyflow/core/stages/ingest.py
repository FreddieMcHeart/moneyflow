"""Ingest: one entry per transaction, nothing decided yet."""

from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Transaction


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


def ingest(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
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
