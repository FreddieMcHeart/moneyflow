"""The person-return kind (A21): an exact description names a person who pays money back."""

from moneyflow.core.kinds._fields import _text
from moneyflow.core.match import matches
from moneyflow.core.model import Rule, Transaction


def apply_rule(rule: Rule, tx: Transaction) -> tuple[str, str] | None:
    """(person, category) when the rule matches `tx`, else None."""
    if not matches(rule.match, tx):
        return None
    where = f"person_return rule {rule.id}"
    return _text(rule.result, "person", where, tx.id), _text(rule.result, "category", where, tx.id)
