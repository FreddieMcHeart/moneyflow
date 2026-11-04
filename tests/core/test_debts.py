from decimal import Decimal

import pytest

from moneyflow.core.build import build
from moneyflow.core.model import BuildRefused, Person, Ruling
from tests.core._builders import account, inputs, ruling, tx

CATS = frozenset({"Debts", "Fuel"})
ALICE = Person(id="alice", display="Alice", opening_balance=Decimal("0"), note=None)


def _debt(id, tx_id, person="alice", kind="lent"):
    return Ruling(id=id, transaction_id=tx_id, kind="debt", payload={"person": person, "kind": kind}, created_by="test")


def _build(txs, rulings, people=(ALICE,), accounts=()):
    ins = inputs(transactions=txs, rulings=rulings, categories=CATS, people=people, accounts=accounts)
    return {(e.transaction_id, e.part): e for e in build(ins)}


def test_debt_lent_is_expense_under_person():
    got = _build((tx(amount="50.00"),), (_debt("d1", "t1"),))[("t1", 0)]
    assert (got.status, got.direction, got.category, got.subcategory) == ("kept", "expense", "Debts", "Alice")
    assert (got.decided_by, got.via, got.amount) == ("ruling:d1", "ruling:d1", -50)


def test_debt_repaid_is_reimbursement():
    txs = (tx("t1", amount="50.00"), tx("t2", amount="30.00", direction="income", day=2))
    got = _build(txs, (_debt("d1", "t1"), _debt("d2", "t2", kind="repaid")))[("t2", 0)]
    assert (got.direction, got.category, got.subcategory, got.amount) == ("reimbursement", "Debts", "Alice", 30)


def test_debt_non_uah_refuses():
    with pytest.raises(BuildRefused):
        _build((tx(currency="USD"),), (_debt("d1", "t1"),), accounts=(account(currency="USD"),))


def test_debt_unknown_person_refuses():
    with pytest.raises(BuildRefused):
        _build((tx(),), (_debt("d1", "t1", person="bob"),))


def test_debt_unknown_kind_refuses():
    with pytest.raises(BuildRefused):
        _build((tx(),), (_debt("d1", "t1", kind="gift"),))


def test_debt_kind_against_wrong_direction_refuses():
    with pytest.raises(BuildRefused):
        _build((tx(direction="income"),), (_debt("d1", "t1", kind="lent"),))


def test_debt_balance_below_zero_refuses():
    txs = (tx("t1", amount="50.00"), tx("t2", amount="60.00", direction="income", day=2))
    with pytest.raises(BuildRefused):
        _build(txs, (_debt("d1", "t1"), _debt("d2", "t2", kind="repaid")))


def test_debt_balance_is_checked_in_date_order():
    # repaid 60 comes before lent 50 in time: the running balance dips below zero even though the total is fine
    txs = (tx("t2", amount="70.00", day=2), tx("t1", amount="60.00", direction="income", day=1))
    with pytest.raises(BuildRefused):
        _build(txs, (_debt("d1", "t1", kind="repaid"), _debt("d2", "t2")))


def test_debt_opening_balance_covers_a_repayment():
    bob = Person(id="bob", display="Bob", opening_balance=Decimal("100"), note=None)
    got = _build((tx(amount="60.00", direction="income"),), (_debt("d1", "t1", person="bob", kind="repaid"),), people=(bob,))
    assert got[("t1", 0)].category == "Debts"


def test_category_and_debt_ruling_on_one_entry_refuse():
    with pytest.raises(BuildRefused):
        _build((tx(),), (_debt("d1", "t1"), ruling("c1", "t1", "Fuel")))


def test_debt_repayment_down_to_exactly_zero_is_allowed():
    txs = (tx("t1", amount="50.00"), tx("t2", amount="50.00", direction="income", day=2))
    got = _build(txs, (_debt("d1", "t1"), _debt("d2", "t2", kind="repaid")))
    assert got[("t2", 0)].category == "Debts"
