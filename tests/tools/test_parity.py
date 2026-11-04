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
        "kept", None, decided_by, "expense", None,
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
    assert _run([_old("a", direction="bogus")], [_new("a")]).differ == [("a", "direction")]


def test_rows_decided_by_other_mechanisms_not_in_population():
    old = [_old("a", rule=r) for r in ("split:x", "refund", "investment-withdrawal", "")]
    result = _run(old, [], raw=set())
    assert (result.population, result.compared, result.out_of_scope) == (0, 0, [])


def test_missing_vs_out_of_scope():
    old = [_old("a"), _old("b"), _old("m-gone", rule="override:manual")]
    new = [_new("a", part=1), _new("c")]
    result = compare(old, {"r1"}, {"a", "b", "c"}, tuple(new))
    assert result.out_of_scope == ["m-gone"]
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
    base = dict(population=2, compared=2, differ=[], missing=[], out_of_scope=[], parts_compared=0)
    return ParityResult(**{**base, **kw})


from tools.parity.compare import compare_excluded  # noqa: E402


def _clean_excluded():
    return compare_excluded(
        [{"id": "a", "rule": "excluded:internal-transfer"}],
        {"a"},
        (LedgerEntry("a", 0, date(2026, 1, 5), "acc1", None, None, Decimal("-1.00"), "UAH",
                     "excluded", "internal-transfer", "core:internal-transfer", "expense", None),),
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
        status, reason if status == "excluded" else None, f"core:{reason}", "expense", None,
    )


def _cx(old, new, raw, not_included=()):
    return compare_excluded(old, set(raw), tuple(new), set(not_included))


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
    r = _cx([_oex("a"), _oex("x#1", "split")], [_nex("a"), _nex("x", "split")], {"a", "k1", "k2", "x"})
    assert r.old_kept == 2  # k1, k2: raw minus the base ids of the old excluded rows {a, x}


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
        transactions=(NS(id="t-on", account_id="on", source="bank"), NS(id="t-off", account_id="off", source="bank"),
                      NS(id="manual-1", account_id="on", source="manual")),
    )
    monkeypatch.setattr(parity_main.db, "connect", lambda url: nullcontext(None))
    monkeypatch.setattr(parity_main.db, "load_ledger", lambda conn: ())
    monkeypatch.setattr(parity_main.db, "load_inputs", lambda conn: inputs)
    for name in ("_raw_ids", "_old_ledger", "_rule_names", "_read_csv", "compare"):
        monkeypatch.setattr(parity_main, name, lambda *a: [])
    seen = {}

    def fake_compare_excluded(old, raw, new, not_included):
        seen.update(not_included=not_included, raw=raw)
        raise _Stop

    monkeypatch.setattr(parity_main, "compare_excluded", fake_compare_excluded)
    with pytest.raises(_Stop):
        parity_main.main(["--old-repo", str(tmp_path), "--db", "unused"])
    assert seen == {"not_included": {"t-off"}, "raw": {"manual-1"}}


# --- 0b.2 buckets (Task 11) ---


def _newe(tid, part=0, category="Cafe", sub=None, decided_by="rule:r1", amount="-500.00", via=None, direction="expense",
          status="kept", reason=None):
    return LedgerEntry(
        tid, part, date(2026, 1, 5), "acc1", category, sub, Decimal(amount), "UAH",
        status, reason, decided_by, direction, via,
    )


def _split_old(base, n, amount, category="Cafe", sub="", rule="split"):
    return _old(f"{base}#{n}", category, sub, rule, amount=amount)


def _split_new(base, part, amount, category="Cafe", sub=None, decided_by=None):
    return _newe(base, part, category, sub, decided_by or f"ruling:split:{base}:{part}", f"-{amount}",
                 via=f"ruling:split:{base}:{part}")


def test_split_parts_compared_as_multiset():
    old = [_split_old("p", 1, "10.00", "Food"), _split_old("p", 2, "20.00", "Cafe"),
           _split_old("p", "rest", "70.00", "Misc", rule="split-remainder")]
    new = [_newe("p", 0, None, None, "core:split", "-100.00", status="excluded", reason="split"),
           _split_new("p", 2, "20.00", "Cafe"), _split_new("p", 1, "10.00", "Food"),
           _newe("p", 3, "Misc", None, "rule:r2", "-70.00")]
    result = compare(old, {"r1"}, {"p"}, tuple(new))
    assert result.differ == [] and result.parts_compared == 1 and result.out_of_scope == []


def test_split_part_amount_mismatch_reported():
    old = [_split_old("p", 1, "10.00"), _split_old("p", 2, "90.00", rule="split-remainder")]
    new = [_newe("p", 0, None, None, "core:split", "-100.00", status="excluded", reason="split"),
           _split_new("p", 1, "11.00"), _newe("p", 2, "Cafe", None, "rule:r2", "-89.00")]
    assert compare(old, {"r1"}, {"p"}, tuple(new)).differ == [("p", "parts")]


def test_split_part_deciding_mechanism_checked():
    old = [_split_old("p", 1, "10.00")]
    new = [_newe("p", 0, None, None, "core:split", "-10.00", status="excluded", reason="split"),
           _newe("p", 1, "Cafe", None, "rule:r1", "-10.00")]
    assert compare(old, {"r1"}, {"p"}, tuple(new)).differ == [("p", "parts")]


def test_credit_interest_part_compared():
    old = [_old("c#interest", "Credit", "", "credit-interest", amount="5.00")]
    ok = _newe("c", 1, "Credit", None, "core:credit-interest", "-5.00", via="rule:cr")
    assert compare(old, set(), {"c"}, (ok,)).differ == []
    bad = _newe("c", 1, "Credit", None, "core:credit-interest", "-6.00", via="rule:cr")
    assert compare(old, set(), {"c"}, (bad,)).differ == [("c", "parts")]


def test_part_added_in_new_only_is_reported():
    extra = _split_new("p", 1, "10.00")
    assert compare([], set(), {"p"}, (extra,)).differ == [("p", "parts")]


def test_refund_bucket_compared_via_shadow_rule():
    old = [_old("a", "Fuel", "", "refund:wog", amount="100.00", direction="reimbursement")]
    ok = _newe("a", 0, "Fuel", None, "rule:wog", "100.00", via="rule:import-cancellation", direction="reimbursement")
    assert compare(old, {"wog"}, {"a"}, (ok,)).differ == []
    wrong = replace(ok, decided_by="rule:other")
    assert compare(old, {"wog"}, {"a"}, (wrong,)).differ == [("a", "decided_by")]
    no_via = replace(ok, via=None)
    assert compare(old, {"wog"}, {"a"}, (no_via,)).differ == [("a", "decided_by")]


def test_investment_withdrawal_and_person_return_buckets():
    inv = _old("i", "Savings", "", "investment-withdrawal:atm", amount="50.00", direction="reimbursement")
    ret = _old("r", "Family", "", "alice-return", amount="30.00", direction="reimbursement")
    new = (
        _newe("i", 0, "Savings", None, "rule:atm", "50.00", via="rule:import-investment-withdrawal", direction="reimbursement"),
        _newe("r", 0, "Family", None, "rule:alice-return", "30.00", via="rule:alice-return", direction="reimbursement"),
    )
    assert compare([inv, ret], {"atm"}, {"i", "r"}, new).differ == []
    swapped = (replace(new[0], decided_by="rule:zzz"), replace(new[1], via=None))
    assert sorted(compare([inv, ret], {"atm"}, {"i", "r"}, swapped).differ) == [("i", "decided_by"), ("r", "decided_by")]


def test_ruling_buckets_checked_through_via():
    old = [_old("d", "Debts", "alice", "debt:manual", amount="40.00"),
           _old("m", "Food", "", "manual-expense", amount="9.00")]
    new = (
        _newe("d", 0, "Debts", "alice", "ruling:debt:d", "-40.00", via="ruling:debt:d"),
        _newe("m", 0, "Food", None, "ruling:manual:m", "-9.00", via=None),
    )
    assert compare(old, set(), {"d", "m"}, new).differ == []
    bad = (replace(new[0], decided_by="rule:x", via=None), new[1])
    assert compare(old, set(), {"d", "m"}, bad).differ == [("d", "decided_by")]


def test_manual_expense_from_real_build_has_no_differ():
    from moneyflow.core.build import build
    from tests.core._builders import inputs, ruling, tx
    led = build(inputs(transactions=(tx("m", source="manual"),), rulings=(ruling("manual:m", "m", "food"),)))
    old = [_old("m", "food", "", "manual-expense", amount="10.00")]
    assert compare(old, set(), {"m"}, led).differ == []
    assert compare(old, set(), {"m"}, (replace(led[0], decided_by="rule:x"),)).differ == [("m", "decided_by")]


@pytest.mark.parametrize("rule,by", [("reimbursement:manual", "ruling:reimbursement:a"), ("debt:manual", "ruling:debt:a")])
def test_reimbursement_and_debt_need_matching_via(rule, by):
    old = [_old("a", "Fuel", "", rule, amount="100.00", direction="reimbursement" if rule.startswith("re") else "expense")]
    sign = "100.00" if rule.startswith("re") else "-100.00"
    ok = _newe("a", 0, "Fuel", None, by, sign, via=by, direction="reimbursement" if rule.startswith("re") else "expense")
    assert compare(old, set(), {"a"}, (ok,)).differ == []
    assert compare(old, set(), {"a"}, (replace(ok, via=None),)).differ == [("a", "decided_by")]
    assert compare(old, set(), {"a"}, (replace(ok, via="ruling:other:a"),)).differ == [("a", "decided_by")]


def test_reimbursement_signed_positive():
    old = [_old("a", "Fuel", "", "reimbursement:manual", amount="100.00", direction="reimbursement")]
    ok = _newe("a", 0, "Fuel", None, "ruling:reimbursement:a", "100.00", via="ruling:reimbursement:a", direction="reimbursement")
    assert compare(old, set(), {"a"}, (ok,)).differ == []
    assert compare(old, set(), {"a"}, (replace(ok, amount=Decimal("-100.00")),)).differ == [("a", "amount")]


def test_credit_principal_in_scope():
    r = _cx([_oex("c1#rest", "credit-principal")], [_nex("c1", "credit-principal")], {"c1"})
    assert (r.population, r.compared, r.differ, r.old_kept) == (1, 1, [], 0)
    wrong = _cx([_oex("c1#rest", "credit-principal")], [_nex("c1", "credit-drawdown")], {"c1"})
    assert wrong.differ == [("c1#rest", "credit-principal", "credit-drawdown")]


def test_split_parent_in_scope_by_base_id():
    r = _cx([_oex("s1#1", "split")], [_nex("s1", "split")], {"s1"})
    assert (r.population, r.differ) == (1, [])
    assert _cx([_oex("s1#1", "split")], [_nex("s1", status="kept")], {"s1"}).differ == [("s1#1", "split", "kept")]


def test_excluded_out_of_scope_counted():
    r = _cx([_oex("a"), _oex("gone"), _oex("gone2#1", "split")], [_nex("a")], {"a"})
    assert r.out_of_scope == ["gone", "gone2#1"] and r.population == 1
