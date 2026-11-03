"""A6/A7/A8 cross-owner detection."""

from collections.abc import Mapping

from moneyflow.core.model import Inputs, LedgerEntry


def cross_owner(entries: list[LedgerEntry], inputs: Inputs, coverage: Mapping[str, object]) -> Mapping[str, str]:
    """Slot: transaction id -> reason. Task 4 replaces it. Finds nothing yet."""
    return {}
