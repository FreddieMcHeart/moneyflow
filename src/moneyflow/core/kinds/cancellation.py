"""The cancellation kind (A20): a `Скасування.` income row is a refund of the purchase named after the prefix."""

from dataclasses import replace

from moneyflow.core.match import matches
from moneyflow.core.model import Rule, Transaction


def apply_rule(rule: Rule, tx: Transaction) -> bool:
    return matches(rule.match, tx)


def shadow(rule: Rule, tx: Transaction) -> Transaction:
    """The original purchase: the description without the prefix, matched as an expense."""
    prefix = str(rule.match.get("description_prefix", ""))
    merchant = tx.description.lstrip()[len(prefix) :].strip()
    return replace(tx, description=merchant, direction="expense")
