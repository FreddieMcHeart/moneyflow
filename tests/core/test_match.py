from datetime import date, datetime
from decimal import Decimal

import pytest
import yaml

from moneyflow.core.match import matches
from moneyflow.core.model import Transaction


def make_tx(**over):
    base = dict(
        id="t1",
        account_id="acc1",
        date=date(2026, 1, 5),
        time=datetime(2026, 1, 5, 10, 0),
        amount=Decimal("10.00"),
        currency="UAH",
        direction="expense",
        mcc=5814,
        description="x",
        comment="",
        source="bank",
    )
    base.update(over)
    return Transaction(**base)


def test_description_contains_reads_comment_case_insensitive():
    tx = make_tx(description="x", comment="paid ACME")
    assert matches({"description_contains": "Acme"}, tx) is True
    assert matches({"description_contains": "absent"}, tx) is False


def test_amount_thresholds_at_boundary():
    tx = make_tx(amount=Decimal("500.00"))
    assert matches({"amount_lt": 500}, tx) is False
    assert matches({"amount_gte": 500}, tx) is True
    assert matches({"amount_lt": 500.01}, tx) is True


def test_amount_threshold_from_yaml_scalar_is_decimal_not_float():
    rule = yaml.safe_load("amount_gte: 0.1")
    assert isinstance(rule["amount_gte"], float)
    tx = make_tx(amount=Decimal("0.10"))
    assert matches(rule, tx) is True
    from moneyflow.core.match import to_decimal

    assert isinstance(to_decimal(rule["amount_gte"]), Decimal)
    assert to_decimal(rule["amount_gte"]) == Decimal("0.1")


def test_all_keys_must_hold():
    tx = make_tx(mcc=5814, direction="income")
    assert matches({"mcc": 5814, "direction": "expense"}, tx) is False
    assert matches({"mcc": 5814, "direction": "income"}, tx) is True
    tx2 = make_tx(mcc=1, direction="expense")
    assert matches({"mcc": 5814, "direction": "expense"}, tx2) is False


def test_mcc_compares_as_int():
    assert matches({"mcc": "5814"}, make_tx(mcc=5814)) is True
    assert matches({"mcc": "5815"}, make_tx(mcc=5814)) is False
    assert matches({"mcc": 5814}, make_tx(mcc=None)) is False


def test_account_key():
    tx = make_tx(account_id="acc1")
    assert matches({"account": "acc1"}, tx) is True
    assert matches({"account": "acc2"}, tx) is False


def test_unknown_key_raises():
    with pytest.raises(ValueError, match="bogus_key"):
        matches({"bogus_key": 1}, make_tx())


def test_direction_key():
    assert matches({"direction": "expense"}, make_tx(direction="expense")) is True
    assert matches({"direction": "income"}, make_tx(direction="expense")) is False


def test_description_prefix_ignores_leading_spaces():
    tx = make_tx(description="   Cancel. shop")
    assert matches({"description_prefix": "Cancel."}, tx) is True
    assert matches({"description_prefix": "shop"}, tx) is False


def test_description_equals_strips():
    tx = make_tx(description="  alice  ")
    assert matches({"description_equals": "alice"}, tx) is True
    assert matches({"description_equals": "alic"}, tx) is False
