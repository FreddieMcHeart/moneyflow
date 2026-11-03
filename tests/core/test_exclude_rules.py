"""A9 other-owner income and the exclude rules (A10 miles, A11 cashback, A14 credit). Synthetic data only."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from moneyflow.core.model import Account, BuildRefused, Owner, Rule, Transaction
from moneyflow.core.stages.exclude import exclude
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import exclude_ruling, inputs

OWNERS = (
    Owner(id="owner_a", display="n", counterparty_strings=("Alpha Test",), budget=True),
    Owner(id="owner_b", display="k", counterparty_strings=("Owner B Test",), budget=False),
)


def acct(id: str, owner: str, currency: str = "UAH") -> Account:
    return Account(id=id, name=id, currency=currency, owner=owner, role="main", include=True, cash_from=None)


ACCOUNTS = (acct("his", "owner_a"), acct("his2", "owner_a"), acct("hers", "owner_b"), acct("his_usd", "owner_a", "USD"))


def row(id, account="his", description="x", amount="100.00", direction="income", day=15, currency="UAH", comment=""):
    return Transaction(
        id=id,
        account_id=account,
        date=date(2026, 1, day),
        time=datetime(2026, 1, day, 12, 0),
        amount=Decimal(amount),
        currency=currency,
        direction=direction,
        mcc=None,
        description=description,
        comment=comment,
    )


def rule(id, reason, priority=10, ordinal=0, paired=False, active=True, kind="exclude", **match):
    result: dict = {"reason": reason}
    if paired:
        result["paired"] = True
    return Rule(id=id, kind=kind, priority=priority, ordinal=ordinal, match=match, result=result, active=active, created_by="test")


MILES = rule("miles", "miles-to-charity", paired=True, direction="income", description_contains="виведення миль")
CASHBACK = rule("cb", "cashback-roundup", description_contains="кешбек")
CREDIT = rule("credit", "credit-drawdown", direction="income", description_contains="кредит до завтра")


def run(*txs, rules=(), rulings=()):
    inp = inputs(transactions=txs, rules=rules, rulings=rulings, accounts=ACCOUNTS, owners=OWNERS)
    return {e.transaction_id: e for e in exclude(ingest([], inp), inp)}


def state(e):
    return (e.status, e.reason, e.decided_by)


def test_other_owner_income_excluded():
    got = run(row("a", "hers"))
    assert state(got["a"]) == ("excluded", "other-owner-income", "core:other-owner-income")


def test_budget_owner_income_kept():
    assert state(run(row("a", "his"))["a"]) == ("unresolved", None, None)


def test_other_owner_expense_is_not_other_owner_income():
    assert run(row("a", "hers", direction="expense"))["a"].status == "unresolved"


def test_miles_pair_excludes_both_legs():
    got = run(row("in", "his", "Виведення миль"), row("out", "his2", "card", direction="expense"), rules=(MILES,))
    assert state(got["in"]) == ("excluded", "miles-to-charity", "rule:miles")
    assert state(got["out"]) == ("excluded", "miles-to-charity", "rule:miles")


def test_miles_without_partner_stays_kept():
    assert run(row("in", "his", "виведення миль"), rules=(MILES,))["in"].status == "unresolved"


def test_miles_partner_must_match_date_amount_currency_and_owner():
    partners = (
        row("p1", "his2", direction="expense", day=14),
        row("p2", "his2", direction="expense", amount="100.01"),
        row("p3", "his_usd", direction="expense", currency="USD"),
        row("p4", "hers", direction="expense"),
        row("p5", "his2"),  # same figures but income
    )
    # A12 (Task 6) pairs opposite legs of one owner within 2 s; keep every row at least an hour apart so only A10 is under test
    partners = tuple(replace(p, time=p.time.replace(hour=13 + i)) for i, p in enumerate(partners))
    got = run(row("in", "his", "виведення миль"), *partners, rules=(MILES,))
    assert got["in"].status == "unresolved"
    assert {got[p].status for p in ("p1", "p2", "p3", "p4")} == {"unresolved"}


def test_miles_partner_already_excluded_is_not_used():
    ruled = (exclude_ruling("x1", "out", "cash-withdrawal"),)
    got = run(row("in", "his", "виведення миль"), row("out", "his2", direction="expense"), rules=(MILES,), rulings=ruled)
    assert got["in"].status == "unresolved"
    assert got["out"].reason == "cash-withdrawal"


def test_two_miles_incomes_do_not_share_one_partner():
    got = run(
        row("in1", "his", "виведення миль"),
        row("in2", "his", "виведення миль"),
        row("out", "his2", direction="expense"),
        rules=(MILES,),
    )
    assert sorted(t for t, e in got.items() if e.status == "excluded") == ["in1", "out"]


def test_miles_partner_may_sit_on_the_same_account():
    got = run(row("in", "his", "виведення миль"), row("out", "his", direction="expense"), rules=(MILES,))
    assert got["in"].status == got["out"].status == "excluded"


def test_expense_with_miles_text_not_excluded():
    got = run(row("a", "his", "виведення миль", direction="expense"), row("b", "his2", direction="expense"), rules=(MILES,))
    assert got["a"].status == "unresolved"
    assert got["b"].status == "unresolved"


def test_cashback_matches_comment():
    got = run(row("a", "his", "shop", direction="expense", comment="Кешбек"), rules=(CASHBACK,))
    assert state(got["a"]) == ("excluded", "cashback-roundup", "rule:cb")


def test_cashback_matches_description_on_either_direction():
    got = run(row("a", "his", "кешбек"), row("b", "his", "кешбек", direction="expense"), rules=(CASHBACK,))
    assert got["a"].status == got["b"].status == "excluded"


def test_credit_drawdown_rule():
    got = run(row("a", "his", "Кредит до завтра"), rules=(CREDIT,))
    assert state(got["a"]) == ("excluded", "credit-drawdown", "rule:credit")


def test_expense_with_drawdown_text_not_excluded():
    assert run(row("a", "his", "кредит до завтра", direction="expense"), rules=(CREDIT,))["a"].status == "unresolved"


def test_instalment_drawdown_without_ruling_stays_kept():
    assert run(row("a", "his", "розстрочка на картку"), rules=(CREDIT,))["a"].status == "unresolved"


def test_instalment_drawdown_with_exclude_ruling_excluded():
    ruled = (exclude_ruling("exclude:credit-drawdown:a", "a", "credit-drawdown"),)
    got = run(row("a", "his", "розстрочка на картку"), rules=(CREDIT,), rulings=ruled)
    assert state(got["a"]) == ("excluded", "credit-drawdown", "ruling:exclude:credit-drawdown:a")


def test_cross_owner_beats_cashback_text():
    out = row("out", "hers", "Alpha Test кешбек", direction="expense")
    back = row("back", "his", "Owner B Test", direction="income")
    filler = (
        row("f1", "his", "f", day=1),
        row("f2", "his", "f", day=28),
        row("f3", "hers", "f", day=1, direction="expense"),
        row("f4", "hers", "f", day=28, direction="expense"),
    )
    got = run(out, back, *filler, rules=(CASHBACK,))
    assert got["out"].reason == "cross-owner-transfer"


def test_cross_owner_claims_its_income_leg_before_other_owner_income():
    out = row("out", "his", "Owner B Test", direction="expense")
    into = row("in", "hers", "Alpha Test", direction="income")
    filler = (
        row("f1", "his", "f", day=1, direction="expense"),
        row("f2", "his", "f", day=28, direction="expense"),
        row("f3", "hers", "f", day=1, direction="expense"),
        row("f4", "hers", "f", day=28, direction="expense"),
    )
    got = run(out, into, *filler)
    assert got["in"].reason == "cross-owner-transfer"
    assert got["out"].reason == "cross-owner-transfer"


def test_other_owner_income_leaves_an_excluded_row_alone():
    ruled = (exclude_ruling("x1", "a", "cash-deposit"),)
    got = run(row("a", "hers"), rulings=ruled)
    assert state(got["a"]) == ("excluded", "cash-deposit", "ruling:x1")


def test_other_owner_income_beats_a_rule():
    got = run(row("a", "hers", "кешбек"), rules=(CASHBACK,))
    assert state(got["a"]) == ("excluded", "other-owner-income", "core:other-owner-income")


def test_ruling_beats_a_rule():
    ruled = (exclude_ruling("x1", "a", "cash-deposit"),)
    got = run(row("a", "his", "кешбек"), rules=(CASHBACK,), rulings=ruled)
    assert state(got["a"]) == ("excluded", "cash-deposit", "ruling:x1")


def test_lower_priority_number_wins_then_ordinal():
    low = rule("low", "r-low", priority=1, ordinal=5, description_contains="shop")
    high = rule("high", "r-high", priority=2, ordinal=0, description_contains="shop")
    assert run(row("a", "his", "shop"), rules=(high, low))["a"].reason == "r-low"
    first = rule("first", "r-first", priority=1, ordinal=0, description_contains="shop")
    assert run(row("a", "his", "shop"), rules=(low, first))["a"].reason == "r-first"


def test_a_rule_without_a_partner_lets_the_next_rule_decide():
    paired = rule("p", "r-paired", priority=1, paired=True, description_contains="shop")
    plain = rule("q", "r-plain", priority=2, description_contains="shop")
    assert run(row("a", "his", "shop"), rules=(paired, plain))["a"].reason == "r-plain"


def test_inactive_and_categorize_rules_are_ignored():
    off = replace(CASHBACK, active=False)
    cat = rule("c", "ignored", kind="categorize", description_contains="кешбек")
    assert run(row("a", "his", "кешбек"), rules=(off, cat))["a"].status == "unresolved"


def test_rules_leave_other_rows_alone():
    got = run(row("a", "his", "кешбек"), row("b", "his", "shop"), rules=(CASHBACK,))
    assert got["b"].status == "unresolved"


@pytest.mark.parametrize(
    "match, message",
    [({}, "rule bad: empty match"), ({"no_such_key": "x"}, "rule bad: unknown match key"), ({"amount_lt": "abc"}, "rule bad")],
)
def test_broken_exclude_rule_refuses_naming_the_rule(match, message):
    bad = rule("bad", "x", **match)
    with pytest.raises(BuildRefused, match=message) as refused:
        run(row("a", "his"), rules=(bad,))
    assert refused.value.transaction_id == "a"
