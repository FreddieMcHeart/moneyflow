"""Synthetic rows only; no real data."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

import tools.parity.__main__ as parity_main
from moneyflow.core.model import LedgerEntry
from tools.parity.compare import ParityResult, compare
from tools.parity.__main__ import excluded_clean, exit_code


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


from tools.parity.compare import compare_excluded  # noqa: E402


def _clean_excluded():
    return compare_excluded(
        [{"id": "a", "rule": "excluded:internal-transfer"}],
        {"a"},
        (LedgerEntry("a", 0, date(2026, 1, 5), "acc1", None, None, Decimal("-1.00"), "UAH",
                     "excluded", "internal-transfer", "core:internal-transfer"),),
        set(),
        set(),
    )


def test_empty_population_fails():
    assert exit_code(_result(population=0, compared=0), _clean_excluded()) != 0
    assert exit_code(_result(population=3, compared=0, out_of_scope=["x", "y", "z"]), _clean_excluded()) != 0


def test_exit_code_nonzero_on_differ():
    assert exit_code(_result(differ=[("a", "category")]), _clean_excluded()) != 0
    assert exit_code(_result(missing=["a"]), _clean_excluded()) != 0
    assert exit_code(_result(), _clean_excluded()) == 0


# --- excluded rows (Task 10) ---


def _oex(tid, reason="internal-transfer"):
    return {"id": tid, "rule": f"excluded:{reason}"}


def _nex(tid, reason="internal-transfer", status="excluded"):
    return LedgerEntry(
        tid, 0, date(2026, 1, 5), "acc1", None, None, Decimal("-1.00"), "UAH",
        status, reason if status == "excluded" else None, f"core:{reason}",
    )


def _cx(old, new, raw, not_included=(), pending=()):
    return compare_excluded(old, set(raw), tuple(new), set(not_included), set(pending))


def test_excluded_same_reason_passes():
    r = _cx([_oex("a")], [_nex("a")], {"a", "k"})
    assert (r.population, r.compared, r.differ) == (1, 1, [])


def test_excluded_different_reason_fails():
    r = _cx([_oex("a", "cashback-roundup")], [_nex("a", "internal-transfer")], {"a"})
    assert r.differ == [("a", "cashback-roundup", "internal-transfer")]


def test_old_excluded_new_kept_fails():
    r = _cx([_oex("a")], [_nex("a", status="kept")], {"a"})
    assert r.differ == [("a", "internal-transfer", "kept")]
    assert _cx([_oex("a")], [], {"a"}).differ == [("a", "internal-transfer", "kept")]


def test_old_kept_new_excluded_fails():
    r = _cx([_oex("a")], [_nex("a"), _nex("k", "cashback-roundup")], {"a", "k"})
    assert r.old_kept_excluded == [("k", "cashback-roundup")]


def test_old_kept_is_raw_minus_old_excluded():
    r = _cx([_oex("a"), _oex("x#1", "split")], [_nex("a")], {"a", "k1", "k2", "x"})
    assert r.old_kept == 3  # k1, k2, x: raw minus the old excluded ids {a, x#1}


def test_next_slice_reasons_reported_and_asserted_not_excluded():
    old = [_oex("s1#1", "split"), _oex("s2#1", "split"), _oex("c1#rest", "credit-principal"), _oex("a")]
    r = _cx(old, [_nex("a")], {"a", "s1", "s2", "c1"})
    assert r.next_slice == {"split": 2, "credit-principal": 1}
    assert r.next_slice_excluded == [] and r.population == 1 and r.derived == 3


def test_split_id_excluded_in_new_fails():
    r = _cx([_oex("s1#1", "split")], [_nex("s1")], {"s1"})
    assert r.next_slice_excluded == [("s1", "split", "internal-transfer")]


def test_credit_principal_base_id_excluded_in_new_fails():
    r = _cx([_oex("c1#rest", "credit-principal")], [_nex("c1", "credit-drawdown")], {"c1"})
    assert r.next_slice_excluded == [("c1", "credit-principal", "credit-drawdown")]


def test_split_pending_bucket_counted_and_not_excluded():
    r = _cx([_oex("a")], [_nex("a"), _nex("w1")], {"a", "w1", "w2"}, pending={"w1", "w2"})
    assert r.split_pending == 2 and r.split_pending_excluded == ["w1"]


def test_account_not_included_allowlisted_by_id():
    new = [_nex("n1", "account-not-included"), _nex("n2", "account-not-included")]
    r = _cx([], new, {"n1", "n2", "k"}, not_included={"n1", "n2"})
    assert r.old_kept_excluded == [] and r.not_included_mismatch == []


def test_account_not_included_on_included_account_fails():
    new = [_nex("n1", "account-not-included"), _nex("bad", "account-not-included")]
    r = _cx([], new, {"n1", "bad"}, not_included={"n1"})
    assert r.not_included_mismatch == ["bad"]
    assert r.old_kept_excluded == [("bad", "account-not-included")]


def test_exit_code_includes_excluded_result():
    ok = _cx([_oex("a")], [_nex("a")], {"a"})
    bad = _cx([_oex("a")], [_nex("a", "cashback-roundup")], {"a"})
    assert exit_code(_result(), ok) == 0
    assert exit_code(_result(), bad) != 0
    assert exit_code(_result(), _cx([], [], set())) != 0  # empty excluded population


def test_exit_code_requires_excluded():
    with pytest.raises(TypeError):
        exit_code(_result())  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "flag, value",
    [
        ("old_kept_excluded", [("k", "cashback-roundup")]),
        ("next_slice_excluded", [("s1", "split", "internal-transfer")]),
        ("split_pending_excluded", ["w1"]),
        ("not_included_mismatch", ["n1"]),
    ],
)
def test_each_excluded_problem_alone_fails_the_run(flag, value):
    clean = _clean_excluded()
    bad = replace(clean, **{flag: value})
    assert excluded_clean(clean) is True
    assert excluded_clean(bad) is False
    assert exit_code(_result(), bad) == 1


class _Stop(Exception):
    pass


def test_main_hands_compare_excluded_the_real_allowlists(monkeypatch, tmp_path):
    inputs = NS(
        accounts=(NS(id="on", include=True), NS(id="off", include=False)),
        transactions=(NS(id="t-on", account_id="on"), NS(id="t-off", account_id="off")),
        rulings=(NS(id="split_pending:p", transaction_id="t-pend"), NS(id="exclude:x", transaction_id="t-on")),
    )
    monkeypatch.setattr(parity_main.db, "connect", lambda url: nullcontext(None))
    monkeypatch.setattr(parity_main.db, "load_ledger", lambda conn: ())
    monkeypatch.setattr(parity_main.db, "load_inputs", lambda conn: inputs)
    for name in ("_raw_ids", "_old_ledger", "_rule_names", "_read_csv", "compare"):
        monkeypatch.setattr(parity_main, name, lambda *a: [])
    seen = {}

    def fake_compare_excluded(old, raw, new, not_included, pending):
        seen.update(not_included=not_included, pending=pending)
        raise _Stop

    monkeypatch.setattr(parity_main, "compare_excluded", fake_compare_excluded)
    with pytest.raises(_Stop):
        parity_main.main(["--old-repo", str(tmp_path), "--db", "unused"])
    assert seen == {"not_included": {"t-off"}, "pending": {"t-pend"}}
