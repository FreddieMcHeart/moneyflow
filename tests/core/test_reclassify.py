import pytest

from moneyflow.core.build import build
from moneyflow.core.model import BuildRefused, Rule, Ruling
from tests.core._builders import exclude_ruling, inputs, rule, ruling, tx

CATS = frozenset({"Fuel", "Income", "Investments", "Reimb"})
CANCEL = "Скасування."


def _rule(id, kind, result, **match):
    return Rule(id=id, kind=kind, priority=10, ordinal=0, match=match, result=result, active=True, created_by="test")


def _cancel():
    return _rule("c1", "cancellation", {}, description_prefix=CANCEL)


def _shopx():
    return rule("shopx", 10, 1, "Fuel", description_contains="ShopX")


def _build(txs, rules, rulings=()):
    return {(e.transaction_id, e.part): e for e in build(inputs(transactions=txs, rules=rules, rulings=rulings, categories=CATS))}


def test_cancellation_takes_merchant_rule_category():
    entry = _build((tx(direction="income", description=f"  {CANCEL} ShopX"),), (_cancel(), _shopx()))[("t1", 0)]
    assert (entry.status, entry.direction, entry.category) == ("kept", "reimbursement", "Fuel")
    assert (entry.decided_by, entry.via) == ("rule:shopx", "rule:c1")
    assert entry.amount > 0


def test_cancellation_shadow_drops_the_prefix():
    exact = rule("exact", 10, 1, "Fuel", description_equals="ShopX")
    entry = _build((tx(direction="income", description=f"{CANCEL} ShopX"),), (_cancel(), exact))[("t1", 0)]
    assert (entry.category, entry.decided_by) == ("Fuel", "rule:exact")


def test_cancellation_shadow_matches_as_expense():
    income_only = rule("inc", 10, 1, "Income", description_contains="ShopX", direction="income")
    entry = _build((tx(direction="income", description=f"{CANCEL} ShopX"),), (_cancel(), income_only))[("t1", 0)]
    assert (entry.status, entry.reason) == ("unresolved", "unmatched")


def test_investment_shadow_matches_as_expense():
    income_only = rule("inc", 10, 2, "Income", description_contains="інжур", direction="income")
    entry = _build((tx(direction="income", description="ІНЖУР"),), (_inv(), income_only))[("t1", 0)]
    assert (entry.status, entry.reason) == ("unresolved", "unmatched")


def test_a_reclassified_row_is_not_reclassified_again():
    both = _rule("p1", "person_return", {"person": "alice", "category": "Reimb"}, description_equals=f"{CANCEL} alice")
    entry = _build((tx(direction="income", description=f"{CANCEL} alice"),), (_cancel(), both))[("t1", 0)]
    assert (entry.via, entry.status, entry.category) == ("rule:c1", "unresolved", None)


def test_cancellation_without_merchant_rule_is_unresolved():
    entry = _build((tx(direction="income", description=f"{CANCEL} Nowhere"),), (_cancel(), _shopx()))[("t1", 0)]
    assert (entry.status, entry.reason, entry.category, entry.direction) == ("unresolved", "unmatched", None, "reimbursement")


def test_cancellation_with_category_ruling_keeps_ruling():
    entry = _build((tx(direction="income", description=f"{CANCEL} ShopX"),), (_cancel(), _shopx()), (ruling("g1", "t1", "Income"),))[("t1", 0)]
    assert (entry.category, entry.decided_by, entry.direction) == ("Income", "ruling:g1", "reimbursement")


def test_person_return_exact_description_only():
    pr = _rule("p1", "person_return", {"person": "alice", "category": "Reimb"}, description_equals="alice")
    got = _build((tx("t1", direction="income", description="  alice "), tx("t2", direction="income", description="alice x")), (pr,))
    hit, miss = got[("t1", 0)], got[("t2", 0)]
    assert (hit.status, hit.direction, hit.category, hit.subcategory) == ("kept", "reimbursement", "Reimb", None)
    assert (hit.decided_by, hit.via) == ("rule:p1", "rule:p1")
    assert (miss.status, miss.direction, miss.via) == ("unresolved", "income", None)


def test_person_return_unknown_category_refuses():
    pr = _rule("p1", "person_return", {"person": "alice", "category": "Nope"}, description_equals="alice")
    with pytest.raises(BuildRefused):
        _build((tx(direction="income", description="alice"),), (pr,))


def test_person_return_without_person_refuses():
    pr = _rule("p1", "person_return", {"category": "Reimb"}, description_equals="alice")
    with pytest.raises(BuildRefused):
        _build((tx(direction="income", description="alice"),), (pr,))


def _inv():
    return _rule("i1", "investment_withdrawal", {}, description_contains="ІНЖУР", direction="income")


def _inv_cat():
    return rule("inv", 10, 2, "Investments", description_contains="інжур")


def test_investment_withdrawal_income_only():
    got = _build((tx("t1", direction="income", description="Від: ІНЖУР"), tx("t2", direction="expense", description="Від: ІНЖУР")), (_inv(), _inv_cat()))
    inc, exp = got[("t1", 0)], got[("t2", 0)]
    assert (inc.direction, inc.category, inc.via) == ("reimbursement", "Investments", "rule:i1")
    assert (exp.direction, exp.via, exp.amount < 0) == ("expense", None, True)


def test_investment_withdrawal_reads_comment():
    t = tx(direction="income", description="transfer")
    t = type(t)(**{**t.__dict__, "comment": "інжур payout"})
    entry = _build((t,), (_inv(), _inv_cat()))[("t1", 0)]
    assert (entry.direction, entry.via) == ("reimbursement", "rule:i1")


def test_investment_withdrawal_skips_a_category_ruling():
    entry = _build((tx(direction="income", description="ІНЖУР"),), (_inv(), _inv_cat()), (ruling("g1", "t1", "Income"),))[("t1", 0)]
    assert (entry.direction, entry.via, entry.category) == ("income", None, "Income")


def test_investment_withdrawal_without_rule_is_unresolved():
    entry = _build((tx(direction="income", description="ІНЖУР"),), (_inv(),))[("t1", 0)]
    assert (entry.status, entry.reason, entry.direction) == ("unresolved", "unmatched", "reimbursement")


def test_reimbursement_reduces_category_spend():
    got = _build(
        (tx("t1", amount="100.00", description="ShopX"), tx("t2", amount="40.00", direction="income", description=f"{CANCEL} ShopX")),
        (_cancel(), _shopx()),
    )
    assert got[("t1", 0)].amount + got[("t2", 0)].amount == -60


def test_excluded_entry_is_not_reclassified():
    entry = _build((tx(direction="income", description=f"{CANCEL} ShopX"),), (_cancel(), _shopx()), (exclude_ruling("x1", "t1", "manual"),))[("t1", 0)]
    assert (entry.status, entry.direction, entry.via) == ("excluded", "income", None)


def test_malformed_reclassify_rule_refuses():
    bad = _rule("c1", "cancellation", {}, nonsense="x")
    with pytest.raises(BuildRefused):
        _build((tx(direction="income"),), (bad,))


def _reimb(id, tx_id, category):
    return Ruling(id=id, transaction_id=tx_id, kind="reimbursement", payload={"category": category}, created_by="test")


def test_reimbursement_ruling_sets_category_and_direction():
    got = _build((tx(direction="income", amount="100.00"),), (), (_reimb("r1", "t1", "Fuel"),))[("t1", 0)]
    assert (got.status, got.direction, got.category, got.subcategory) == ("kept", "reimbursement", "Fuel", None)
    assert (got.decided_by, got.via) == ("ruling:r1", "ruling:r1")
    assert got.amount == 100


def test_reimbursement_ruling_unknown_category_refuses():
    with pytest.raises(BuildRefused):
        _build((tx(direction="income"),), (), (_reimb("r1", "t1", "Nope"),))


def test_reimbursement_ruling_on_an_excluded_entry_refuses():
    with pytest.raises(BuildRefused):
        _build((tx(direction="income"),), (), (exclude_ruling("x1", "t1", "manual"), _reimb("r1", "t1", "Fuel")))


def test_two_reimbursement_rulings_on_one_transaction_refuse():
    with pytest.raises(BuildRefused):
        _build((tx(direction="income"),), (), (_reimb("r1", "t1", "Fuel"), _reimb("r2", "t1", "Fuel")))
