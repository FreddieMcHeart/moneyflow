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
        "kept", None, decided_by, "expense", None, None, None, None, None,
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
                     "excluded", "internal-transfer", "core:internal-transfer", "expense", None, None, None, None, None),),
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
        status, reason if status == "excluded" else None, f"core:{reason}", "expense", None, None, None, None, None,
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
    monkeypatch.setattr(parity_main.db, "load_inputs", lambda conn, *, today: inputs)
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
        status, reason, decided_by, direction, via, None, None, None, None,
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


# --- 0b.3 cycles, UAH amounts, outside-cycle rows, totals, month summaries (Task 9) ---

from tools.parity import compare as cmp_mod  # noqa: E402


def _oc(tid, month="2026-01", uah="500.00", **kw):
    return {**_old(tid, **kw), "budget_month": month, "amount_uah": uah}


def _nc(tid, month="2026-01", uah="-500.00", status="kept", **kw):
    return replace(_newe(tid, **kw), budget_month=month, amount_uah=None if uah is None else Decimal(uah), status=status)


def test_budget_month_mismatch_reported():
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a"),)) == []
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a", month="2026-02"),)) == [("a", "budget_month")]
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a", month=None),)) == [("a", "budget_month")]
    assert cmp_mod.compare_cycle_fields([_oc("a", month="")], (_nc("a", month=None),)) == []


def test_amount_uah_mismatch_reported():
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a", uah="-500.0"),)) == []
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a", uah="-500.01"),)) == [("a", "amount_uah")]
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a", uah=None),)) == [("a", "amount_uah")]
    income = _oc("i", uah="70.00", direction="income")
    assert cmp_mod.compare_cycle_fields([income], (_nc("i", uah="70.00", direction="income", amount="70.00"),)) == []
    assert cmp_mod.compare_cycle_fields([income], (_nc("i", uah="-70.00", direction="income", amount="70.00"),)) == [("i", "amount_uah")]


def test_cycle_fields_skip_parts_and_unmatched_ids():
    assert cmp_mod.compare_cycle_fields([_oc("p#1", month="2026-09")], (_nc("p"),)) == []
    assert cmp_mod.compare_cycle_fields([_oc("gone")], ()) == []


def _out_old(tid, category="", sub="", amount="500.00"):
    return _old(tid, category, sub, "unmatched", amount=amount)


def _out_new(tid, category=None, sub=None, amount="-500.00", status="outside_cycle"):
    return _nc(tid, month=None, uah=None, status=status, category=category, sub=sub, amount=amount)


def test_outside_cycle_rows_compared():
    ok = cmp_mod.compare_outside([_out_old("a"), _out_old("b", "Food", "x", "7.00")],
                                 (_out_new("a"), _out_new("b", "Food", "x", "-7.00")))
    assert (ok.population, ok.compared, ok.differ) == (2, 2, [])
    cases = [
        (_out_new("a", category="Food"), "category"),
        (_out_new("a", sub="x"), "subcategory"),
        (_out_new("a", amount="-501.00"), "amount"),
    ]
    for new, field in cases:
        assert cmp_mod.compare_outside([_out_old("a")], (new,)).differ == [("a", field)]
    assert cmp_mod.compare_outside([_out_old("a")], ()).differ == [("a", "absent")]
    extra = cmp_mod.compare_outside([_out_old("a")], (_out_new("a"), _out_new("z")))
    assert extra.differ == [("z", "extra")]


def test_outside_cycle_vs_unresolved_reported():
    unresolved = _out_new("a", status="unresolved")
    assert cmp_mod.compare_outside([_out_old("a")], (unresolved,)).differ == [("a", "status")]
    kept = _out_new("a", status="kept")
    assert cmp_mod.compare_outside([_out_old("a")], (kept,)).differ == [("a", "status")]
    assert cmp_mod.compare_outside([_out_old("a")], (_out_new("a"),)).differ == []


def _totals_old():
    return [_oc("a", category="Food", sub="x", amount="10.00", uah="10.00"),
            _oc("b", category="Food", sub="x", amount="5.00", uah="5.00"),
            _oc("c", category="Pay", direction="income", amount="100.00", uah="100.00"),
            _oc("u", category="", amount="9.00", uah="9.00"),
            _oc("p#1", category="Food", sub="x", amount="1.00", uah="1.00")]


def _totals_new():
    return (_nc("a", category="Food", sub="x", amount="-10.00", uah="-10.00"),
            _nc("b", category="Food", sub="x", amount="-5.00", uah="-5.00"),
            _nc("c", category="Pay", amount="100.00", uah="100.00", direction="income"),
            _nc("u", category=None, amount="-9.00", uah="-9.00", status="unresolved"),
            _nc("p", part=1, category="Food", sub="x", amount="-1.00", uah="-1.00"),
            _nc("p", part=0, category=None, amount="-1.00", uah=None, status="excluded"))


def test_totals_per_cycle_category_currency():
    assert cmp_mod.compare_totals(_totals_old(), _totals_new()) == []
    new = list(_totals_new())
    mutations = {
        "month": replace(new[0], budget_month="2026-02"),
        "category": replace(new[0], category="Fun"),
        "subcategory": replace(new[0], subcategory="y"),
        "currency": replace(new[0], currency="USD"),
        "amount": replace(new[0], amount=Decimal("-10.01")),
    }
    for name, entry in mutations.items():
        assert cmp_mod.compare_totals(_totals_old(), (entry, *new[1:])) != [], name
    key = ("2026-01", "Food", "x", "UAH")
    assert cmp_mod.compare_totals(_totals_old(), (replace(new[0], amount=Decimal("-10.01")), *new[1:])) == [
        (key, "amount")]
    extra = _nc("z", category="Fun", amount="-1.00", uah="-1.00")
    assert cmp_mod.compare_totals(_totals_old(), (*new, extra)) == [(("2026-01", "Fun", None, "UAH"), "amount"),
                                                                    (("2026-01", "Fun", None, "UAH"), "amount_uah")]


def test_totals_uah_mismatch_reported():
    new = list(_totals_new())
    key = ("2026-01", "Food", "x", "UAH")
    off = replace(new[0], amount_uah=Decimal("-10.01"))
    assert cmp_mod.compare_totals(_totals_old(), (off, *new[1:])) == [(key, "amount_uah")]
    unconverted = replace(new[0], amount_uah=None)
    assert cmp_mod.compare_totals(_totals_old(), (unconverted, *new[1:])) == [(key, "amount_uah")]


def _summary(month="2026-01", **kw):
    base = dict(budget_month=month, cycle_start=date(2026, 1, 1), cycle_end=date(2026, 1, 31), cycle_open=False,
                days_total=31, days_left=0, income={"UAH": Decimal("100.00")}, total_actual={"UAH": Decimal("40.00")},
                cash_in_hand=None)
    return NS(**{**base, **kw})


def _summary_new(**kw):
    kw = {"total_actual": {"UAH": Decimal("-40.00")}, **kw}
    return _summary(**kw)


def test_month_summaries_compared_field_by_field():
    assert cmp_mod.compare_summaries([_summary()], [_summary_new()]) == []
    mutations = {
        "income": {"income": {"UAH": Decimal("101.00")}},
        "total_actual": {"total_actual": {"UAH": Decimal("-41.00")}},
        "cycle_start": {"cycle_start": date(2026, 1, 2)},
        "cycle_end": {"cycle_end": date(2026, 1, 30)},
        "cycle_open": {"cycle_open": True},
        "days_total": {"days_total": 30},
        "days_left": {"days_left": 1},
        "cash_in_hand": {"cash_in_hand": Decimal("0")},
    }
    for field, change in mutations.items():
        assert cmp_mod.compare_summaries([_summary()], [_summary_new(**change)]) == [("2026-01", field)], field
    assert cmp_mod.compare_summaries([_summary()], []) == [("2026-01", "absent")]
    assert cmp_mod.compare_summaries([], [_summary_new()]) == [("2026-01", "absent")]
    # old total_actual counts spending positive, the new ledger signs it negative: equal magnitude must match
    assert cmp_mod.compare_summaries([_summary(total_actual={"UAH": Decimal("40.00")})],
                                     [_summary_new(total_actual={"UAH": Decimal("40.00")})]) == [("2026-01", "total_actual")]


def test_per_cycle_report_line():
    old = [_summary("2026-01"), _summary("2026-02", cycle_open=True, cycle_end=None)]
    lines = cmp_mod.cycle_lines(
        old,
        [_oc("a"), _oc("b", month="2026-02")],
        [("a", "category"), ("b", "amount")],
        [(("2026-02", "Food", None, "UAH"), "amount")],
        [("2026-01", "income"), ("2026-01", "days_left")],
    )
    assert [line.text() for line in lines] == [
        "2026-01 rows differ 1, totals differ 0, summary differ 2",
        "2026-02 rows differ 1, totals differ 1, summary differ 0",
    ]
    assert [line.open for line in lines] == [False, True]


def test_open_cycle_is_reported_not_gated():
    old = [_summary("2026-01"), _summary("2026-02", cycle_open=True, cycle_end=None)]
    outside = cmp_mod.OutsideParityResult(population=1, compared=1, differ=[])
    open_only = cmp_mod.cycle_lines(old, [_oc("b", month="2026-02")], [("b", "amount")], [], [])
    assert cmp_mod.cycles_clean(open_only, outside) is True
    closed = cmp_mod.cycle_lines(old, [_oc("a")], [("a", "amount")], [], [])
    assert cmp_mod.cycles_clean(closed, outside) is False
    assert cmp_mod.cycles_clean(open_only, replace(outside, differ=[("a", "status")])) is False
    assert cmp_mod.cycles_clean([], outside) is False  # no cycles at all is not a pass


def test_row_with_unknown_month_is_gated():
    old = [_summary("2026-01")]
    lines = cmp_mod.cycle_lines(old, [], [("ghost", "category")], [], [])
    outside = cmp_mod.OutsideParityResult(population=0, compared=0, differ=[])
    assert cmp_mod.cycles_clean(lines, outside) is False
    assert lines[-1].text() == "(no cycle) rows differ 1, totals differ 0, summary differ 0"


def test_old_summaries_wiring(tmp_path):
    seen = {}
    rows = [NS(account="manual-cash-uah"), NS(account="acc1")]
    old = NS(
        ledger=NS(load_ledger_dir=lambda path: seen.setdefault("ledger", path) and rows),
        accounts=NS(load_accounts=lambda path: {"x": NS(id="inc", role="income"), "y": NS(id="s", role="spending")}),
        cycles=NS(
            load_attribution=lambda path: {"t": "2026-01"},
            build_cycles=lambda r, attribution, income: seen.update(cycle_args=(r, attribution, income)) or ["cycle"],
        ),
        cash=NS(
            CASH_ACCOUNT="manual-cash-uah", CASH_OPENING=Decimal("1"), CASH_CUTOVER=date(2026, 11, 1),
            load_cash_movements=lambda path: ["movement"],
            cash_balance=lambda *a: seen.update(cash_args=a) or {"2026-01": None},
        ),
        model=NS(month_summaries=lambda r, c, today, cash_in_hand: seen.update(final=(r, c, today, cash_in_hand)) or ["s"]),
    )
    today = date(2026, 11, 4)
    assert parity_main._old_summaries(tmp_path, old, today) == ["s"]
    assert seen["cycle_args"] == (rows, {"t": "2026-01"}, {"inc"})
    assert seen["cash_args"] == (["movement"], [rows[0]], ["cycle"], Decimal("1"), date(2026, 11, 1), today)
    assert seen["final"] == (rows, ["cycle"], today, {"2026-01": None})


def test_old_modules_load_from_the_old_repo_path(tmp_path):
    with pytest.raises(ModuleNotFoundError):
        parity_main._load_old_modules(tmp_path / "no-such-repo")


def _stub_main(monkeypatch, tmp_path, cycle_pass):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "excluded.csv").write_text("id,rule\n", encoding="utf-8")
    monkeypatch.setattr(parity_main.db, "connect", lambda url: nullcontext(None))
    monkeypatch.setattr(parity_main.db, "load_ledger", lambda conn: ())
    monkeypatch.setattr(parity_main.db, "load_inputs", lambda conn, *, today: NS(accounts=(), transactions=()))
    for name in ("_raw_ids", "_old_ledger", "_rule_names"):
        monkeypatch.setattr(parity_main, name, lambda *a: [])
    monkeypatch.setattr(parity_main, "compare", lambda *a: _result())
    monkeypatch.setattr(parity_main, "compare_excluded", lambda *a: _clean_excluded())
    monkeypatch.setattr(parity_main, "_cycle_passes", cycle_pass)


def _cycle_parity(differ=(), month_differ=0):
    outside = cmp_mod.OutsideParityResult(population=2, compared=2, differ=list(differ))
    lines = cmp_mod.cycle_lines([_summary("2026-01")], [_oc("a")], [("a", "x")] * month_differ, [], [])
    return cmp_mod.CycleParity(outside=outside, lines=lines)


def test_main_prints_the_cycle_passes_and_keeps_the_exit_code(monkeypatch, tmp_path, capsys):
    _stub_main(monkeypatch, tmp_path, lambda *a: _cycle_parity())
    with pytest.raises(SystemExit) as stop:
        parity_main.main(["--old-repo", str(tmp_path), "--db", "unused"])
    out = capsys.readouterr().out
    assert stop.value.code == 0
    assert "outside cycle population: 2" in out
    assert "outside cycle differ: 0" in out
    assert "2026-01 rows differ 0, totals differ 0, summary differ 0" in out


@pytest.mark.parametrize("kind", ["outside", "closed cycle"])
def test_main_exits_one_when_a_cycle_pass_differs(monkeypatch, tmp_path, kind):
    parity = _cycle_parity(differ=[("a", "status")]) if kind == "outside" else _cycle_parity(month_differ=1)
    _stub_main(monkeypatch, tmp_path, lambda *a: parity)
    with pytest.raises(SystemExit) as stop:
        parity_main.main(["--old-repo", str(tmp_path), "--db", "unused"])
    assert stop.value.code == 1


def test_main_hands_the_cycle_passes_the_ledger_rows_and_the_row_differ(monkeypatch, tmp_path):
    seen = {}

    def spy(old_repo, data, old_rows, new, inputs, differ):
        seen.update(repo=old_repo, data=data, rows=old_rows, differ=differ)
        return _cycle_parity()

    _stub_main(monkeypatch, tmp_path, spy)
    monkeypatch.setattr(parity_main, "_old_ledger", lambda *a: ["row"])
    monkeypatch.setattr(parity_main, "compare", lambda *a: _result(differ=[("a", "category")]))
    with pytest.raises(SystemExit):
        parity_main.main(["--old-repo", str(tmp_path), "--db", "unused"])
    assert seen == {"repo": tmp_path, "data": tmp_path / "data", "rows": ["row"], "differ": [("a", "category")]}


def test_totals_keep_the_old_currency_apart():
    usd_old = [_oc("a", category="Food", currency="USD", amount="10.00", uah="440.00")]
    usd_new = (replace(_nc("a", category="Food", amount="-10.00", uah="-440.00"), currency="USD"),)
    assert cmp_mod.compare_totals(usd_old, usd_new) == []
    uah_new = (replace(usd_new[0], currency="UAH"),)
    key_old, key_new = ("2026-01", "Food", None, "USD"), ("2026-01", "Food", None, "UAH")
    assert [k for k, _ in cmp_mod.compare_totals(usd_old, uah_new)] == [key_new, key_new, key_old, key_old]


def test_unconverted_totals_never_match_each_other():
    blank = [_oc("a", category="Food", uah="")]
    unconverted = (_nc("a", category="Food", uah=None),)
    assert cmp_mod.compare_totals(blank, unconverted) == [(("2026-01", "Food", None, "UAH"), "amount_uah")]


def test_uah_compared_to_the_kopeck_not_beyond():
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a", uah="-500.004"),)) == []
    assert cmp_mod.compare_cycle_fields([_oc("a")], (_nc("a", uah="-500.006"),)) == [("a", "amount_uah")]
