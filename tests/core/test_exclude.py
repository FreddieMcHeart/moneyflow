import pytest

from moneyflow.core.build import build
from moneyflow.core.model import BuildRefused
from tests.core._builders import account, exclude_ruling, inputs, tx


def test_exclude_ruling_excludes_with_its_reason():
    ledger = build(inputs(transactions=(tx(),), rulings=(exclude_ruling("x1", "t1", "cash-withdrawal"),)))
    (e,) = ledger
    assert (e.status, e.reason, e.category) == ("excluded", "cash-withdrawal", None)


def test_exclude_ruling_decided_by_names_ruling():
    (e,) = build(inputs(transactions=(tx(),), rulings=(exclude_ruling("exclude:cash-withdrawal:t1", "t1", "cash-withdrawal"),)))
    assert e.decided_by == "ruling:exclude:cash-withdrawal:t1"


def test_exclude_ruling_on_not_included_account_refuses():
    inp = inputs(
        transactions=(tx(),),
        rulings=(exclude_ruling("x1", "t1", "cash-withdrawal"),),
        accounts=(account(include=False),),
    )
    with pytest.raises(BuildRefused, match="exclude ruling on an excluded entry") as refused:
        build(inp)
    assert refused.value.transaction_id == "t1"
