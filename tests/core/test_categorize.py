import pytest

from moneyflow.core.build import build
from moneyflow.core.model import BuildRefused
from tests.core._builders import inputs, rule, ruling, tx


def _one(inp):
    (entry,) = build(inp)
    return entry


def test_equal_priority_first_in_file_wins():
    r0 = rule("r0", 10, 0, "food", description_contains="shop")
    r1 = rule("r1", 10, 1, "misc", description_contains="shop")
    e = _one(inputs(transactions=(tx(),), rules=(r1, r0)))
    assert e.category == "food"
    assert e.decided_by == "rule:r0"


def test_lower_priority_wins_over_file_order():
    r0 = rule("r0", 20, 0, "food", description_contains="shop")
    r1 = rule("r1", 10, 1, "misc", description_contains="shop")
    e = _one(inputs(transactions=(tx(),), rules=(r0, r1)))
    assert e.category == "misc"
    assert e.decided_by == "rule:r1"


def test_ruling_beats_rule():
    r0 = rule("r0", 10, 0, "food", "pizza", description_contains="shop")
    e = _one(inputs(transactions=(tx(),), rules=(r0,), rulings=(ruling("g1", "t1", "misc"),)))
    assert e.category == "misc"
    assert e.subcategory is None
    assert e.decided_by == "ruling:g1"
    assert e.status == "kept"


def test_rule_gives_subcategory_and_kept():
    r0 = rule("r0", 10, 0, "food", "pizza", description_contains="shop")
    e = _one(inputs(transactions=(tx(),), rules=(r0,)))
    assert (e.category, e.subcategory, e.status) == ("food", "pizza", "kept")


def test_unmatched_stays_unresolved():
    r0 = rule("r0", 10, 0, "food", description_contains="zzz")
    e = _one(inputs(transactions=(tx(),), rules=(r0,)))
    assert e.status == "unresolved"
    assert e.category is None and e.decided_by is None


def test_unknown_category_refuses():
    r0 = rule("r0", 10, 0, "nope", description_contains="shop")
    with pytest.raises(BuildRefused) as err:
        build(inputs(transactions=(tx(),), rules=(r0,)))
    assert err.value.transaction_id == "t1"


def test_unknown_category_in_ruling_refuses():
    with pytest.raises(BuildRefused):
        build(inputs(transactions=(tx(),), rulings=(ruling("g1", "t1", "nope"),)))


def test_inactive_rule_is_ignored():
    r0 = rule("r0", 10, 0, "food", description_contains="shop")
    r0 = type(r0)(**{**r0.__dict__, "active": False})
    assert _one(inputs(transactions=(tx(),), rules=(r0,))).status == "unresolved"


def test_excluded_entry_is_not_categorized():
    from tests.core._builders import account

    r0 = rule("r0", 10, 0, "food", description_contains="shop")
    inp = inputs(transactions=(tx(),), rules=(r0,), accounts=(account(include=False),))
    e = _one(inp)
    assert e.status == "excluded" and e.category is None


def test_two_rulings_for_one_transaction_refuse():
    rulings = (ruling("g1", "t1", "food"), ruling("g2", "t1", "misc"))
    with pytest.raises(BuildRefused, match="more than one ruling") as refused:
        build(inputs(transactions=(tx(),), rulings=rulings))
    assert refused.value.transaction_id == "t1"


def test_ruling_on_excluded_entry_refuses():
    from tests.core._builders import account

    inp = inputs(transactions=(tx(),), rulings=(ruling("g1", "t1", "food"),), accounts=(account(include=False),))
    with pytest.raises(BuildRefused, match="ruling on an excluded entry"):
        build(inp)


def test_broken_rule_value_refuses_naming_the_rule():
    bad = rule("r9", 10, 0, "food", amount_lt="abc")
    with pytest.raises(BuildRefused, match="rule r9") as refused:
        build(inputs(transactions=(tx(),), rules=(bad,)))
    assert refused.value.transaction_id == "t1"


def test_empty_match_rule_refuses():
    with pytest.raises(BuildRefused, match="rule r0: empty match"):
        build(inputs(transactions=(tx(),), rules=(rule("r0", 10, 0, "food"),)))


def test_categorize_ignores_non_category_rulings():
    from tests.core._builders import exclude_ruling, white_card_ruling

    rulings = (exclude_ruling("x1", "t2", "cash-withdrawal"), white_card_ruling("w1", "t1"))
    ledger = build(inputs(transactions=(tx("t1"), tx("t2")), rulings=rulings))
    by_id = {e.transaction_id: e for e in ledger}
    assert by_id["t1"].status == "unresolved"
    assert by_id["t2"].status == "excluded" and by_id["t2"].reason == "cash-withdrawal"


def test_category_and_white_card_ruling_on_one_leg_do_not_refuse():
    from tests.core._builders import white_card_ruling

    rulings = (ruling("g1", "t1", "food"), white_card_ruling("w1", "t1"))
    (e,) = build(inputs(transactions=(tx(),), rulings=rulings))
    assert (e.status, e.category, e.decided_by) == ("kept", "food", "ruling:g1")


def test_category_ruling_on_excluded_entry_still_refuses():
    from tests.core._builders import exclude_ruling

    rulings = (exclude_ruling("x1", "t1", "cash-withdrawal"), ruling("g1", "t1", "food"))
    with pytest.raises(BuildRefused, match="ruling on an excluded entry"):
        build(inputs(transactions=(tx(),), rulings=rulings))
