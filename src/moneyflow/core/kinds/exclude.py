"""The exclude kind: how a ruling or a rule excludes a transaction."""

from collections.abc import Callable

from moneyflow.core.kinds._fields import _text
from moneyflow.core.match import matches
from moneyflow.core.model import Rule, Ruling, Transaction


def apply_ruling(ruling: Ruling) -> str:
    return _text(ruling.payload, "reason", f"exclude ruling {ruling.id}", ruling.transaction_id)


def apply_rule(rule: Rule, tx: Transaction, partner_lookup: Callable[[Transaction], Transaction | None]) -> str | None:
    """The reason when the rule matches `tx`, else None. A `paired` rule also needs a partner."""
    if not matches(rule.match, tx):
        return None
    if rule.result.get("paired") and partner_lookup(tx) is None:
        return None
    return _text(rule.result, "reason", f"exclude rule {rule.id}", tx.id)
