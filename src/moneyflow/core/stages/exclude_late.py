"""A17: exclude an Investments row on an account of a non-budget owner, after categorize."""

from moneyflow.core.model import Inputs, LedgerEntry


def exclude_late(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """Slot: Task 8 replaces it and wires it into STAGES. Returns the entries unchanged."""
    return entries
