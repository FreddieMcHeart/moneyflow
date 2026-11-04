"""The investment-withdrawal kind (A22): money coming back out of an investment is a sale, not income."""

from dataclasses import replace

from moneyflow.core.match import matches
from moneyflow.core.model import Rule, Transaction


def apply_rule(rule: Rule, tx: Transaction) -> bool:
    return matches(rule.match, tx)


def shadow(rule: Rule, tx: Transaction) -> Transaction:
    """The deposit side: the same text, matched as an expense."""
    return replace(tx, direction="expense")
