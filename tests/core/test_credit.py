from dataclasses import replace
from decimal import Decimal

import pytest

from moneyflow.core.build import build
from moneyflow.core.credit import pair_credit
from moneyflow.core.model import BuildRefused, Rule
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import account, inputs, owner, tx

CATS = frozenset({"Credit", "Food"})
PREFIX = "Repay "


def _repay_rule():
    return Rule(id="cr1", kind="credit_repayment", priority=10, ordinal=0, match={"description_prefix": PREFIX}, result={}, active=True, created_by="test")


def _drawdown(id="d1", amount="100.00", description="loan x", day=1, **kw):
    return tx(id=id, amount=amount, direction="income", description=description, day=day, **kw)


def _repayment(id="r1", amount="110.00", description=f"{PREFIX}loan x", day=5, **kw):
    return tx(id=id, amount=amount, direction="expense", description=description, day=day, **kw)


def _pair(txs, **kw):
    """Run pair_credit on ingested entries where every income row named d* is an excluded credit-drawdown."""
    inp = inputs(transactions=tuple(txs), rules=(_repay_rule(),), categories=CATS, **kw)
    entries = [
        replace(e, status="excluded", reason="credit-drawdown", decided_by="rule:x") if e.transaction_id.startswith("d") else e
        for e in ingest([], inp)
    ]
    return pair_credit(entries, inp)


def _parts(out, tid):
    return sorted((e for e in out if e.transaction_id == tid), key=lambda e: e.part)


def test_repayment_splits_into_principal_and_interest():
    out = _pair([_drawdown(), _repayment()])
    principal, interest = _parts(out, "r1")
    assert (principal.part, principal.status, principal.reason, principal.amount) == (0, "excluded", "credit-principal", Decimal("100.00"))
    assert principal.decided_by == "rule:cr1"
    assert (interest.part, interest.status, interest.category, interest.amount) == (1, "kept", "Credit", Decimal("10.00"))
    assert (interest.decided_by, interest.direction) == ("core:credit-interest", "expense")


def test_zero_interest_makes_no_interest_part():
    out = _pair([_drawdown(amount="110.00"), _repayment()])
    parts = _parts(out, "r1")
    assert [(p.part, p.status, p.amount) for p in parts] == [(0, "excluded", Decimal("110.00"))]


def test_repayment_without_drawdown_stays_kept():
    out = _pair([_repayment()])
    (entry,) = _parts(out, "r1")
    assert (entry.part, entry.status, entry.amount, entry.via) == (0, "unresolved", Decimal("110.00"), None)


def test_drawdown_used_once_for_two_repayments():
    out = _pair([_drawdown(), _repayment("r1", day=5), _repayment("r2", day=6)])
    assert [e.reason for e in _parts(out, "r1")][0] == "credit-principal"
    (second,) = _parts(out, "r2")
    assert (second.status, second.via) == ("unresolved", None)


def test_two_drawdowns_each_take_the_earliest_unused():
    out = _pair([_drawdown("d1", day=1), _drawdown("d2", amount="50.00", day=2), _repayment("r1", day=5), _repayment("r2", amount="60.00", day=6)])
    assert _parts(out, "r1")[0].amount == Decimal("100.00")
    assert _parts(out, "r2")[0].amount == Decimal("50.00")


def test_drawdown_larger_than_repayment_not_paired():
    out = _pair([_drawdown(amount="120.00"), _repayment()])
    (entry,) = _parts(out, "r1")
    assert entry.status == "unresolved"


def test_drawdown_of_other_owner_not_paired():
    other = replace(account("acc2"), owner="o2")
    out = _pair([_drawdown(account_id="acc2"), _repayment()], accounts=(account(), other), owners=(owner(), owner("o2", budget=False)))
    (entry,) = _parts(out, "r1")
    assert entry.status == "unresolved"


def test_drawdown_of_other_currency_not_paired():
    usd = replace(account("acc2", currency="USD"), owner="o")
    out = _pair([_drawdown(account_id="acc2", currency="USD"), _repayment()], accounts=(account(), usd))
    (entry,) = _parts(out, "r1")
    assert entry.status == "unresolved"


def test_later_drawdown_not_paired():
    out = _pair([_drawdown(day=9), _repayment(day=5)])
    (entry,) = _parts(out, "r1")
    assert entry.status == "unresolved"


def test_drawdown_with_other_description_not_paired():
    out = _pair([_drawdown(description="loan y"), _repayment()])
    (entry,) = _parts(out, "r1")
    assert entry.status == "unresolved"


def test_parts_sum_to_repayment():
    out = _pair([_drawdown(amount="87.35"), _repayment(amount="99.99")])
    assert sum(p.amount for p in _parts(out, "r1")) == Decimal("99.99")


def test_unknown_interest_category_refuses():
    inp = inputs(transactions=(_drawdown(), _repayment()), rules=(_repay_rule(),), categories=frozenset({"Food"}))
    entries = [replace(e, status="excluded", reason="credit-drawdown") if e.transaction_id == "d1" else e for e in ingest([], inp)]
    with pytest.raises(BuildRefused, match="unknown category Credit"):
        pair_credit(entries, inp)


def test_build_pairs_through_reclassify_and_signs_the_parts():
    drawdown_rule = Rule(
        id="dd", kind="exclude", priority=10, ordinal=1, match={"description_contains": "loan", "direction": "income"},
        result={"reason": "credit-drawdown"}, active=True, created_by="test",
    )
    inp = inputs(transactions=(_drawdown(), _repayment()), rules=(_repay_rule(), drawdown_rule), categories=CATS)
    ledger = {(e.transaction_id, e.part): e for e in build(inp)}
    assert (ledger["r1", 0].status, ledger["r1", 0].amount) == ("excluded", Decimal("-100.00"))
    assert (ledger["r1", 1].status, ledger["r1", 1].category, ledger["r1", 1].amount) == ("kept", "Credit", Decimal("-10.00"))


def test_drawdown_at_the_same_moment_not_paired():
    out = _pair([_drawdown(day=5), _repayment(day=5)])
    (entry,) = _parts(out, "r1")
    assert entry.status == "unresolved"
