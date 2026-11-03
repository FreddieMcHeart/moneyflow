"""The categorize kind: how a rule or a ruling decides a category."""

from moneyflow.core.kinds._fields import _text
from moneyflow.core.match import matches
from moneyflow.core.model import Rule, Ruling, Transaction


def apply_rule(rule: Rule, tx: Transaction) -> tuple[str, str | None] | None:
    if not matches(rule.match, tx):
        return None
    category = _text(rule.result, "category", f"categorize rule {rule.id}", tx.id)
    return category, rule.result.get("subcategory")  # type: ignore[return-value]


def apply_ruling(ruling: Ruling) -> tuple[str, str | None]:
    category = _text(ruling.payload, "category", f"categorize ruling {ruling.id}", ruling.transaction_id)
    return category, ruling.payload.get("subcategory")
