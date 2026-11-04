import pytest

from moneyflow.core.build import build
from moneyflow.core.model import BuildRefused, Rule, Ruling
from tests.core._builders import inputs, tx


def _rule(kind: str, result: dict) -> Rule:
    return Rule(id="r1", kind=kind, priority=1, ordinal=0, match={"description_contains": "shop"}, result=result, active=True, created_by="test")  # type: ignore[arg-type]


def _ruling(kind: str, payload: dict) -> Ruling:
    return Ruling(id="g1", transaction_id="t1", kind=kind, payload=payload, created_by="test")  # type: ignore[arg-type]


def test_exclude_rule_without_reason_refuses():
    with pytest.raises(BuildRefused, match=r"exclude rule r1: reason missing or not a string"):
        build(inputs(transactions=(tx(),), rules=(_rule("exclude", {}),)))


def test_exclude_rule_with_non_str_reason_refuses():
    with pytest.raises(BuildRefused, match=r"exclude rule r1: reason missing or not a string"):
        build(inputs(transactions=(tx(),), rules=(_rule("exclude", {"reason": True}),)))


def test_exclude_ruling_without_reason_refuses():
    with pytest.raises(BuildRefused, match=r"exclude ruling g1: reason missing or not a string") as refused:
        build(inputs(transactions=(tx(),), rulings=(_ruling("exclude", {"note": None}),)))
    assert refused.value.transaction_id == "t1"


def test_category_ruling_without_category_refuses():
    with pytest.raises(BuildRefused, match=r"categorize ruling g1: category missing or not a string") as refused:
        build(inputs(transactions=(tx(),), rulings=(_ruling("category", {"subcategory": None}),)))
    assert refused.value.transaction_id == "t1"


def test_categorize_rule_without_category_refuses():
    with pytest.raises(BuildRefused, match=r"categorize rule r1: category missing or not a string"):
        build(inputs(transactions=(tx(),), rules=(_rule("categorize", {"subcategory": None}),)))
