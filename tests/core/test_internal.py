"""A12/A13 internal transfers: a pair of opposite legs on one owner's accounts within 2 seconds."""

from dataclasses import replace
from datetime import datetime, timedelta

from moneyflow.core.build import build
from moneyflow.core.internal import internal_pairs
from moneyflow.core.model import Account, LedgerEntry, Rule
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import account, exclude_ruling, inputs, owner, ruling, tx, white_card_ruling

T0 = datetime(2026, 1, 1, 12, 0, 0)


def acc(id: str, owner_id: str = "o") -> Account:
    return replace(account(id), owner=owner_id)


def leg(id: str, account_id: str, direction: str, seconds: float = 0, description: str = "shop"):
    return replace(tx(id, account_id=account_id, direction=direction, description=description), time=T0 + timedelta(seconds=seconds))


def pairs(*txs, accounts=None, owners=None, rulings=()):
    inp = inputs(
        transactions=tuple(txs),
        accounts=accounts or (acc("a1"), acc("a2")),
        owners=owners or (owner(),),
        rulings=rulings,
    )
    return internal_pairs(ingest([], inp), inp)


def test_pair_at_exactly_two_seconds():
    assert pairs(leg("out", "a1", "expense"), leg("in", "a2", "income", 2)) == {"out", "in"}


def test_no_pair_beyond_two_seconds():
    assert pairs(leg("out", "a1", "expense"), leg("in", "a2", "income", 2.001)) == frozenset()


def test_different_owner_does_not_pair():
    accounts = (acc("a1"), acc("a2", "p"))
    owners = (owner(), owner("p", budget=False))
    assert pairs(leg("out", "a1", "expense"), leg("in", "a2", "income"), accounts=accounts, owners=owners) == frozenset()


def test_unregistered_owner_pairs_with_nothing():
    accounts = (acc("a1", "ghost"), acc("a2", "ghost"))
    assert pairs(leg("out", "a1", "expense"), leg("in", "a2", "income"), accounts=accounts) == frozenset()


def test_same_account_does_not_pair():
    assert pairs(leg("out", "a1", "expense"), leg("in", "a1", "income")) == frozenset()


def test_same_direction_does_not_pair():
    assert pairs(leg("x", "a1", "expense"), leg("y", "a2", "expense")) == frozenset()


def test_amount_and_currency_are_not_consulted():
    usd = replace(acc("a2"), currency="USD")
    inc = replace(leg("in", "a2", "income", 1), amount=tx().amount * 40, currency="USD")
    assert pairs(leg("out", "a1", "expense"), inc, accounts=(acc("a1"), usd)) == {"out", "in"}


def test_vet_bill_next_to_a_white_card_leg_is_not_excluded():
    """A white-card leg stays spending: it is out of the pool, so a nearby opposite row does not pair with it."""
    rulings = (white_card_ruling("white_card:out", "out"), ruling("override:out", "out", "food"))
    assert pairs(leg("out", "a1", "expense"), leg("vet", "a2", "income", 1), rulings=rulings) == frozenset()


def test_already_excluded_rows_are_out_of_the_pool():
    rulings = (exclude_ruling("x1", "out", "cash-withdrawal"),)
    inp = inputs(
        transactions=(leg("out", "a1", "expense"), leg("in", "a2", "income", 1)),
        accounts=(acc("a1"), acc("a2")),
        rulings=rulings,
    )
    entries: list[LedgerEntry] = [
        replace(e, status="excluded", reason="cash-withdrawal", decided_by="ruling:x1") if e.transaction_id == "out" else e
        for e in ingest([], inp)
    ]
    assert internal_pairs(entries, inp) == frozenset()


def test_pair_is_excluded_as_internal_transfer_in_a_build():
    inp = inputs(
        transactions=(leg("out", "a1", "expense"), leg("in", "a2", "income", 1)),
        accounts=(acc("a1"), acc("a2")),
    )
    got = {e.transaction_id: (e.status, e.reason, e.decided_by) for e in build(inp)}
    assert got["out"] == got["in"] == ("excluded", "internal-transfer", "core:internal-transfer")


def test_blessed_leg_beats_cashback_text():
    cashback = Rule(
        id="cashback", kind="exclude", priority=1, ordinal=1, match={"description_contains": "cashback"},
        result={"reason": "cashback-roundup"}, active=True, created_by="test",
    )
    inp = inputs(
        transactions=(leg("in", "a1", "income", 0, "cashback"), leg("out", "a2", "expense", 30)),
        accounts=(acc("a1"), acc("a2")),
        rules=(cashback,),
        rulings=(exclude_ruling("exclude:internal-transfer:in", "in", "internal-transfer"),),
    )
    got = {e.transaction_id: (e.reason, e.decided_by) for e in build(inp)}
    assert got["in"] == ("internal-transfer", "ruling:exclude:internal-transfer:in")


def test_a_ruling_that_is_not_a_white_card_keeps_the_leg_in_the_pool():
    rulings = (ruling("override:out", "out", "food"),)
    assert pairs(leg("out", "a1", "expense"), leg("in", "a2", "income", 1), rulings=rulings) == {"out", "in"}
