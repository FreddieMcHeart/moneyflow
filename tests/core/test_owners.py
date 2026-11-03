import pytest

from moneyflow.core.model import BuildRefused
from moneyflow.core.owners import budget_owner, is_budget_owner_account, owner_of
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import account, inputs, owner, tx


def test_budget_owner_is_the_one_flagged():
    inp = inputs(owners=(owner("a", budget=False), owner("b", budget=True)))
    assert budget_owner(inp).id == "b"


def test_no_budget_owner_refuses():
    inp = inputs(transactions=(tx(),), owners=(owner("a", budget=False),))
    with pytest.raises(BuildRefused, match="exactly one budget owner"):
        budget_owner(inp)
    with pytest.raises(BuildRefused, match="exactly one budget owner"):
        ingest([], inp)


def test_two_budget_owners_refuse():
    inp = inputs(transactions=(tx(),), owners=(owner("a"), owner("b")))
    with pytest.raises(BuildRefused, match="exactly one budget owner"):
        budget_owner(inp)
    with pytest.raises(BuildRefused, match="exactly one budget owner"):
        ingest([], inp)


def test_owner_of_reads_the_account():
    assert owner_of("acc1", inputs()) == "o"


def test_owner_of_unknown_account_refuses():
    with pytest.raises(BuildRefused, match="unknown account nope"):
        owner_of("nope", inputs())


def test_is_budget_owner_account():
    inp = inputs(
        accounts=(account("mine"), account("theirs")),
        owners=(owner("o", budget=True), owner("p", budget=False)),
    )
    assert is_budget_owner_account("mine", inp) is True
    from dataclasses import replace

    other = replace(inp, accounts=(inp.accounts[0], replace(inp.accounts[1], owner="p")))
    assert is_budget_owner_account("theirs", other) is False
