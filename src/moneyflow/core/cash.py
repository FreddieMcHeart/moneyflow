"""A5 cash movements.

From the cash account's cutover date on, an ATM withdrawal is a transfer to the cash account, not spending.
The cash account is data: the account whose `cash_from` is set. MCC and marker are the bank's wording of one
mechanism and are used nowhere else. A hand ruling of any kind on a transaction beats the signature.
The old `cash_deposits:` block has no input here: a deposit is an `exclude` ruling with reason `cash-deposit`.
"""

from collections.abc import Mapping
from datetime import date

from moneyflow.core.model import Account, BuildRefused, Inputs, LedgerEntry, Transaction
from moneyflow.core.owners import owner_of

WITHDRAWAL_MCC = 6011
WITHDRAWAL_MARKER = "Видача готівки"
WITHDRAWAL = "cash-withdrawal"


def _cash_account(inputs: Inputs) -> tuple[Account, date] | None:
    found = [(a, a.cash_from) for a in inputs.accounts if a.cash_from is not None]
    if len(found) > 1:
        raise BuildRefused("more than one cash account", None)
    return found[0] if found else None


def _is_withdrawal(tx: Transaction, account: Account, cutover: date, inputs: Inputs) -> bool:
    if tx.date < cutover or tx.direction != "expense":
        return False
    if tx.mcc != WITHDRAWAL_MCC and WITHDRAWAL_MARKER not in tx.description:
        return False
    if tx.account_id == account.id or tx.currency != account.currency:
        return False
    return owner_of(tx.account_id, inputs) == account.owner


def cash_movements(entries: list[LedgerEntry], inputs: Inputs) -> Mapping[str, str]:
    """Transaction id -> reason, for every open entry that is a withdrawal to the cash account."""
    found = _cash_account(inputs)
    if found is None:
        return {}
    account, cutover = found
    by_id = {t.id: t for t in inputs.transactions}
    hand = {r.transaction_id for r in inputs.rulings}
    return {
        e.transaction_id: WITHDRAWAL
        for e in entries
        if e.status == "unresolved" and e.transaction_id not in hand and _is_withdrawal(by_id[e.transaction_id], account, cutover, inputs)
    }
