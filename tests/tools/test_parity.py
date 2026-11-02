"""Synthetic rows only; no real data."""

from datetime import date
from decimal import Decimal

import pytest

from moneyflow.core.model import LedgerEntry
from tools.parity.compare import ParityResult, compare
from tools.parity.__main__ import exit_code


def _old(tid, category="Cafe", sub="", rule="r1", currency="UAH", amount="500.00", direction="expense"):
    return {
        "id": tid, "category": category, "subcategory": sub, "rule": rule,
        "currency": currency, "amount": amount, "direction": direction,
    }


def _new(tid, category="Cafe", sub=None, decided_by="rule:r1", currency="UAH", amount="-500.00", part=0):
    return LedgerEntry(
        tid, part, date(2026, 1, 5), "acc1", category, sub, Decimal(amount), currency,
        "kept", None, decided_by,
    )


def _run(old, new, rules=("r1",), raw=None):
    ids = {r["id"] for r in old} if raw is None else raw
    return compare(old, set(rules), set(ids), tuple(new))


def test_identical_rows_pass():
    result = _run([_old("a"), _old("b", direction="income", amount="7.00")],
                  [_new("a"), _new("b", amount="7.00")])
    assert (result.population, result.compared) == (2, 2)
    assert result.differ == [] and result.missing == [] and result.out_of_scope == []


def test_swapped_categories_are_caught():
    old = [_old("a", "Cafe"), _old("b", "Food")]
    new = [_new("a", "Food"), _new("b", "Cafe")]
    assert sorted(_run(old, new).differ) == [("a", "category"), ("b", "category")]


def test_empty_subcategory_equals_none():
    assert _run([_old("a", sub="")], [_new("a", sub=None)]).differ == []
    assert _run([_old("a", sub="x")], [_new("a", sub=None)]).differ == [("a", "subcategory")]


def test_expense_sign():
    assert _run([_old("a")], [_new("a", amount="500.00")]).differ == [("a", "amount")]
    inc = _run([_old("a", direction="income")], [_new("a", amount="-500.00")])
    assert inc.differ == [("a", "amount")]


def test_currency_and_unknown_direction():
    assert _run([_old("a", currency="USD")], [_new("a")]).differ == [("a", "currency")]
    assert _run([_old("a", direction="reimbursement")], [_new("a")]).differ == [("a", "direction")]


def test_rows_decided_by_other_mechanisms_not_in_population():
    old = [_old("a", rule=r) for r in ("debt:manual", "split:x", "refund:m", "investment-withdrawal", "")]
    result = _run(old, [], raw=set())
    assert (result.population, result.compared, result.out_of_scope) == (0, 0, [])


def test_missing_vs_out_of_scope():
    old = [_old("a"), _old("b"), _old("m#rest", rule="override:manual")]
    new = [_new("a", part=1), _new("c")]
    result = compare(old, {"r1"}, {"a", "b", "c"}, tuple(new))
    assert result.out_of_scope == ["m#rest"]
    assert result.missing == ["a", "b"]
    assert (result.population, result.compared) == (3, 2)


def test_dropped_ruling_with_same_category_is_caught():
    old = [_old("a", rule="override:manual")]
    assert _run(old, [_new("a", decided_by="rule:x")]).differ == [("a", "decided_by")]
    assert _run(old, [_new("a", decided_by="ruling:override:a")]).differ == []


def test_rule_name_must_match_decided_by():
    assert _run([_old("a")], [_new("a", decided_by="rule:r2")]).differ == [("a", "decided_by")]
    assert _run([_old("a")], [_new("a", decided_by="ruling:o")]).differ == [("a", "decided_by")]


def _result(**kw):
    base = dict(population=2, compared=2, differ=[], missing=[], out_of_scope=[])
    return ParityResult(**{**base, **kw})


def test_empty_population_fails():
    assert exit_code(_result(population=0, compared=0)) != 0
    assert exit_code(_result(population=3, compared=0, out_of_scope=["x", "y", "z"])) != 0


def test_exit_code_nonzero_on_differ():
    assert exit_code(_result(differ=[("a", "category")])) != 0
    assert exit_code(_result(missing=["a"])) != 0
    assert exit_code(_result()) == 0
