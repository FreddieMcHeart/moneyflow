"""Arithmetic: the sign. Expense is negative, income stays positive."""

from dataclasses import replace

from moneyflow.core.model import Inputs, LedgerEntry


def arithmetic(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    direction = {t.id: t.direction for t in inputs.transactions}
    return [replace(e, amount=-e.amount) if direction[e.transaction_id] == "expense" else e for e in entries]
