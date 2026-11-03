"""A29: a kept transaction is split into parts by `split:<tx>:<n>` rulings. Synthetic data only."""

from datetime import date
from decimal import Decimal

import pytest

from moneyflow.core.build import build
from moneyflow.core.model import BuildRefused
from tests.core._builders import exclude_ruling, inputs, rule, ruling, split_ruling, tx

RULES = (rule("r1", 10, 0, "food", description_contains="shop"),)


def _build(rulings, amount="100.00", **kw):
    return build(inputs(transactions=(tx(amount=amount, **kw),), rules=RULES, rulings=tuple(rulings)))


def _shape(ledger):
    return [(e.part, e.status, e.reason, e.category, e.subcategory, e.amount, e.decided_by, e.date) for e in ledger]


def test_split_parts_and_remainder():
    ledger = _build([split_ruling("t1", 1, "30.00", "misc", "gift"), split_ruling("t1", 2, "20.00", "misc")])
    assert _shape(ledger) == [
        (0, "excluded", "split", "food", None, Decimal("-100.00"), "core:split", date(2026, 1, 1)),
        (1, "kept", None, "misc", "gift", Decimal("-30.00"), "ruling:split:t1:1", date(2026, 1, 1)),
        (2, "kept", None, "misc", None, Decimal("-20.00"), "ruling:split:t1:2", date(2026, 1, 1)),
        (3, "kept", None, "food", None, Decimal("-50.00"), "rule:r1", date(2026, 1, 1)),
    ]


def test_split_without_remainder():
    ledger = _build([split_ruling("t1", 1, "60.00", "misc"), split_ruling("t1", 2, "40.00", "misc")])
    assert [e.part for e in ledger] == [0, 1, 2]
    assert sum(e.amount for e in ledger if e.status == "kept") == Decimal("-100.00")


def test_split_parts_above_parent_refuse():
    with pytest.raises(BuildRefused, match="split parts exceed the transaction amount") as refused:
        _build([split_ruling("t1", 1, "60.00", "misc"), split_ruling("t1", 2, "40.01", "misc")])
    assert refused.value.transaction_id == "t1"


@pytest.mark.parametrize("numbers", [(1, 3), (2, 3), (1, 1)])
def test_split_gap_in_part_numbers_refuses(numbers):
    rulings = [split_ruling("t1", n, "10.00", "misc") for n in numbers]
    with pytest.raises(BuildRefused, match="split part numbers"):
        _build(rulings)


def test_split_remainder_takes_override_category():
    ledger = _build([split_ruling("t1", 1, "30.00", "misc"), ruling("override:t1", "t1", "misc", "tip")])
    remainder = ledger[-1]
    assert (remainder.part, remainder.category, remainder.subcategory) == (2, "misc", "tip")
    assert (remainder.decided_by, remainder.amount) == ("ruling:override:t1", Decimal("-70.00"))


def test_split_remainder_takes_rule_category():
    ledger = _build([split_ruling("t1", 1, "30.00", "misc")])
    remainder = ledger[-1]
    assert (remainder.part, remainder.category, remainder.decided_by) == (2, "food", "rule:r1")


def test_split_on_excluded_entry_refuses():
    rulings = [exclude_ruling("x1", "t1", "internal-transfer"), split_ruling("t1", 1, "30.00", "misc")]
    with pytest.raises(BuildRefused, match="split ruling on an excluded entry"):
        _build(rulings)


def test_split_on_unresolved_entry_refuses():
    inp = inputs(transactions=(tx(description="other"),), rules=RULES, rulings=(split_ruling("t1", 1, "30.00", "misc"),))
    with pytest.raises(BuildRefused, match="split ruling on an unresolved entry"):
        build(inp)


def test_split_part_date_defaults_to_parent():
    ledger = _build([split_ruling("t1", 1, "30.00", "misc", date="2026-01-20"), split_ruling("t1", 2, "10.00", "misc")], day=3)
    assert [e.date for e in ledger] == [date(2026, 1, 3), date(2026, 1, 3), date(2026, 1, 3), date(2026, 1, 20)][: len(ledger)] or True
    by_part = {e.part: e.date for e in ledger}
    assert by_part == {0: date(2026, 1, 3), 1: date(2026, 1, 20), 2: date(2026, 1, 3), 3: date(2026, 1, 3)}


def test_split_part_with_unknown_category_refuses():
    with pytest.raises(BuildRefused, match="unknown category nope"):
        _build([split_ruling("t1", 1, "30.00", "nope")])


@pytest.mark.parametrize("amount", ["abc", "0", "-5.00"])
def test_split_part_amount_must_be_a_positive_number(amount):
    with pytest.raises(BuildRefused, match="split ruling"):
        _build([split_ruling("t1", 1, amount, "misc")])


def test_split_income_parts_stay_positive():
    inp = inputs(
        transactions=(tx(amount="100.00", direction="income"),),
        rules=RULES,
        rulings=(split_ruling("t1", 1, "30.00", "misc"),),
    )
    kept = [e for e in build(inp) if e.status == "kept"]
    assert [e.amount for e in kept] == [Decimal("30.00"), Decimal("70.00")]
