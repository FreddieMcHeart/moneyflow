"""A5 cash movements after the cutover. Synthetic data only."""

from dataclasses import replace
from datetime import date

import pytest

from moneyflow.core.build import build
from moneyflow.core.cash import WITHDRAWAL_MARKER, WITHDRAWAL_MCC, cash_movements
from moneyflow.core.model import Account, BuildRefused
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import exclude_ruling, inputs, owner, ruling, tx

CUTOVER = date(2026, 1, 10)


def acct(id: str, owner_id: str = "o", currency: str = "UAH", cash_from: date | None = None, include: bool = True) -> Account:
    return Account(id=id, name=id, currency=currency, owner=owner_id, role="main", include=include, cash_from=cash_from)


CARD = acct("card")
CASH = acct("cash", cash_from=CUTOVER)
ACCOUNTS = (CARD, CASH)


def withdrawal(id: str = "w1", day: int = 12, account_id: str = "card", mcc: int | None = WITHDRAWAL_MCC, description: str = "atm", **kw):
    return replace(tx(id=id, account_id=account_id, day=day, description=description, **kw), mcc=mcc)


def run(*txs, rulings=(), accounts=ACCOUNTS, owners=()):
    inp = inputs(transactions=txs, rulings=rulings, accounts=accounts, owners=owners)
    return cash_movements(ingest([], inp), inp)


def test_withdrawal_after_cutover_excluded():
    assert run(withdrawal()) == {"w1": "cash-withdrawal"}


def test_withdrawal_after_cutover_is_excluded_by_the_build():
    inp = inputs(transactions=(withdrawal(),), accounts=ACCOUNTS)
    (e,) = build(inp)
    assert (e.status, e.reason, e.decided_by) == ("excluded", "cash-withdrawal", "core:cash-withdrawal")


def test_withdrawal_before_cutover_kept():
    assert run(withdrawal(day=9)) == {}


def test_withdrawal_on_cutover_day():
    assert run(withdrawal(day=10)) == {"w1": "cash-withdrawal"}


def test_ruling_beats_cash_signature():
    assert run(withdrawal(), rulings=(ruling("override:w1", "w1", "food"),)) == {}


def test_exclude_ruling_keeps_its_own_reason_in_the_build():
    inp = inputs(transactions=(withdrawal(),), accounts=ACCOUNTS, rulings=(exclude_ruling("x1", "w1", "cash-deposit"),))
    (e,) = build(inp)
    assert (e.reason, e.decided_by) == ("cash-deposit", "ruling:x1")


def test_no_cash_account_does_nothing():
    assert run(withdrawal(account_id="card2"), accounts=(CARD, acct("card2"))) == {}


def test_the_marker_alone_is_enough():
    assert run(withdrawal(mcc=None, description=f"x {WITHDRAWAL_MARKER} y")) == {"w1": "cash-withdrawal"}


def test_the_mcc_alone_is_enough():
    assert run(withdrawal(mcc=WITHDRAWAL_MCC, description="plain")) == {"w1": "cash-withdrawal"}


def test_neither_mcc_nor_marker_is_kept():
    assert run(withdrawal(mcc=None, description="plain")) == {}


def test_an_income_row_is_kept():
    assert run(withdrawal(direction="income")) == {}


def test_a_row_on_the_cash_account_itself_is_kept():
    assert run(withdrawal(account_id="cash")) == {}


def test_another_currency_is_kept():
    assert run(withdrawal(account_id="usd", currency="USD"), accounts=(acct("usd", currency="USD"), CASH)) == {}


def test_another_owners_account_is_kept():
    accounts = (acct("theirs", owner_id="p"), CASH)
    assert run(withdrawal(account_id="theirs"), accounts=accounts, owners=(owner(), owner("p", budget=False))) == {}


def test_an_account_not_included_stays_not_included():
    inp = inputs(transactions=(withdrawal(),), accounts=(acct("card", include=False), CASH))
    (e,) = build(inp)
    assert (e.status, e.reason) == ("excluded", "account-not-included")


def test_two_cash_accounts_refuse():
    with pytest.raises(BuildRefused, match="more than one cash account"):
        run(withdrawal(), accounts=(CARD, CASH, acct("cash2", cash_from=CUTOVER)))
