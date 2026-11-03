"""The exclude kind: how a ruling or a rule excludes a transaction."""

from collections.abc import Callable

from moneyflow.core.model import Rule, Ruling, Transaction


def apply_ruling(ruling: Ruling) -> str:
    reason = ruling.payload["reason"]
    assert reason is not None
    return reason


def apply_rule(rule: Rule, tx: Transaction, partner_lookup: Callable[[Transaction], Transaction | None]) -> str | None:
    """Slot for the A10/A11/A14 exclude rules; Task 5 replaces it. Excludes nothing yet."""
    return None
