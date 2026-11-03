"""A12/A13 internal transfers: money moving between two of one owner's own accounts is not spending.

Two legs pair when they sit on different accounts of the same registered owner, run in opposite
directions, and are at most 2 seconds apart (inclusive). Amount, currency and text are not read:
a cross-currency move has different amounts on its two legs by construction.

The pool is every entry still unresolved minus the white-card legs. Rows an earlier mechanism
already excluded (cross-owner, other-owner income, miles, cashback, a hand ruling) are not in it,
and a white-card leg stays spending, so a nearby opposite row cannot drag it out of the budget.
A13 blessings are `exclude` rulings with reason `internal-transfer`, applied before this runs.

`Transaction.time` is a naive UTC datetime here; the importer and the DB layer do the conversion.
"""

from collections import defaultdict
from datetime import timedelta

from moneyflow.core.model import Inputs, LedgerEntry, Transaction

TOLERANCE = timedelta(seconds=2)


def _pool(entries: list[LedgerEntry], inputs: Inputs) -> list[Transaction]:
    open_ids = {e.transaction_id for e in entries if e.status == "unresolved"}
    white = {r.transaction_id for r in inputs.rulings if r.kind == "white_card"}
    return [t for t in inputs.transactions if t.id in open_ids and t.id not in white]


def internal_pairs(entries: list[LedgerEntry], inputs: Inputs) -> frozenset[str]:
    """Ids of every pool transaction that has at least one transfer partner (existence, not assignment)."""
    registered = {o.id for o in inputs.owners}
    account_owner = {a.id: a.owner for a in inputs.accounts}
    by_owner: dict[str, list[Transaction]] = defaultdict(list)
    for tx in _pool(entries, inputs):
        owner = account_owner.get(tx.account_id)
        if owner in registered:  # an unregistered owner pairs with nothing: under-pair, never over-pair
            by_owner[owner].append(tx)
    paired: set[str] = set()
    for txs in by_owner.values():
        for a in txs:
            for b in txs:
                if a.account_id != b.account_id and a.direction != b.direction and abs(a.time - b.time) <= TOLERANCE:
                    paired.add(a.id)
                    break
    return frozenset(paired)
