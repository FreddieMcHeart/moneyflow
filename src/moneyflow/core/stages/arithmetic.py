"""Arithmetic: the sign. Expense is negative; income and reimbursement stay positive."""

from dataclasses import replace

from moneyflow.core.model import Inputs, LedgerEntry


def arithmetic(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    return [replace(e, amount=-e.amount) if e.direction == "expense" else e for e in entries]
