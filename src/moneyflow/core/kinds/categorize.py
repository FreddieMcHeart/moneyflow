"""The categorize kind: how a rule or a ruling decides a category."""

from moneyflow.core.match import matches
from moneyflow.core.model import Rule, Ruling, Transaction


def apply_rule(rule: Rule, tx: Transaction) -> tuple[str, str | None] | None:
    if not matches(rule.match, tx):
        return None
    category = rule.result["category"]
    assert category is not None
    return category, rule.result.get("subcategory")


def apply_ruling(ruling: Ruling) -> tuple[str, str | None]:
    category = ruling.payload["category"]
    assert category is not None
    return category, ruling.payload.get("subcategory")
