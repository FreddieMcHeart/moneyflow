from decimal import Decimal

import pytest

from moneyflow.core.model import BuildRefused
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import account, inputs, ruling, split_ruling, tx, white_card_ruling


def test_unknown_account_refuses():
    with pytest.raises(BuildRefused) as e:
        ingest([], inputs(transactions=(tx(account_id="nope"),)))
    assert e.value.transaction_id == "t1"


def test_duplicate_transaction_refuses():
    with pytest.raises(BuildRefused) as e:
        ingest([], inputs(transactions=(tx("t1"), tx("t1"))))
    assert e.value.transaction_id == "t1"


def test_currency_mismatch_refuses():
    with pytest.raises(BuildRefused) as e:
        ingest([], inputs(transactions=(tx(currency="USD"),)))
    assert e.value.transaction_id == "t1"


def test_not_included_account_is_excluded():
    inp = inputs(transactions=(tx(),), accounts=(account(include=False),))
    (entry,) = ingest([], inp)
    assert entry.status == "excluded"
    assert entry.reason == "account-not-included"


def test_ingest_shape():
    (entry,) = ingest([], inputs(transactions=(tx(amount="12.50"),)))
    assert entry.part == 0
    assert entry.amount == Decimal("12.50")
    assert entry.currency == "UAH"
    assert entry.category is None and entry.subcategory is None
    assert entry.decided_by is None and entry.reason is None
    assert entry.status == "unresolved"
    assert entry.account_id == "acc1"


def test_white_card_without_category_or_split_ruling_refuses():
    inp = inputs(transactions=(tx(),), rulings=(white_card_ruling("white_card:t1", "t1"),))
    with pytest.raises(BuildRefused, match="white-card leg without a category or split ruling") as refused:
        ingest([], inp)
    assert refused.value.transaction_id == "t1"


def test_white_card_with_split_and_no_category_ruling_builds():
    rulings = (white_card_ruling("white_card:t1", "t1"), split_ruling("t1", 1, "4.00", "misc"))
    (entry,) = ingest([], inputs(transactions=(tx(),), rulings=rulings))
    assert entry.status == "unresolved"


def test_white_card_with_split_pending_ruling_passes_a4():
    rulings = (white_card_ruling("white_card:t1", "t1"), ruling("split_pending:t1", "t1", "misc"))
    (entry,) = ingest([], inputs(transactions=(tx(),), rulings=rulings))
    assert entry.status == "unresolved"


def test_white_card_with_override_ruling_passes_a4():
    rulings = (white_card_ruling("white_card:t1", "t1"), ruling("override:t1", "t1", "food"))
    (entry,) = ingest([], inputs(transactions=(tx(),), rulings=rulings))
    assert entry.status == "unresolved"
