"""A17: exclude an Investments row on an account of a non-budget owner, after categorize."""

from dataclasses import replace

from moneyflow.core.model import Inputs, LedgerEntry
from moneyflow.core.owners import is_budget_owner_account

REASON = "other-owner-investment"


def exclude_late(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A kept Investments entry on another owner's account becomes excluded, keeping its category."""
    return [
        replace(e, status="excluded", reason=REASON, decided_by=f"core:{REASON}")
        if e.status == "kept" and e.category == "Investments" and not is_budget_owner_account(e.account_id, inputs)
        else e
        for e in entries
    ]
