"""A23, A24: attributed income anchors cycles; every counted row gets a budget month. Synthetic data only."""

from datetime import date

import pytest

from moneyflow.core.build import build
from moneyflow.core.cycles import Cycle, build_cycles, cycle_of
from moneyflow.core.model import BuildRefused
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import account, attribute_ruling, inputs, reimbursement_ruling, ruling, tx

ACCOUNTS = (account("acc1"), account("inc", role="income"))


def _salary(id: str, month: int, day: int, **kw):
    return tx(id, account_id="inc", direction="income", amount="500.00", month=month, day=day, hour=9, **kw)


def _inputs(transactions, rulings=(), accounts=ACCOUNTS):
    return inputs(transactions=tuple(transactions), rulings=tuple(rulings), accounts=accounts)


def _cycles(transactions, rulings):
    inp = _inputs(transactions, rulings)
    return build_cycles(ingest([], inp), inp)


def _by_id(ledger):
    return {e.transaction_id: e for e in ledger}


def test_one_cycle_per_attributed_month_anchored_on_earliest_income():
    got = _cycles(
        [_salary("s2", 2, 5), _salary("s1", 2, 3), _salary("s3", 3, 1)],
        [attribute_ruling("s2", "2026-02"), attribute_ruling("s1", "2026-02"), attribute_ruling("s3", "2026-03")],
    )
    assert got == (Cycle("2026-02", date(2026, 2, 3), date(2026, 2, 28)), Cycle("2026-03", date(2026, 3, 1), None))


def test_cycle_ends_day_before_next_anchor():
    got = _cycles(
        [_salary("a", 1, 10), _salary("b", 2, 20), _salary("c", 4, 1)],
        [attribute_ruling("a", "2026-01"), attribute_ruling("b", "2026-02"), attribute_ruling("c", "2026-04")],
    )
    assert [(c.budget_month, c.start, c.end) for c in got] == [
        ("2026-01", date(2026, 1, 10), date(2026, 2, 19)),
        ("2026-02", date(2026, 2, 20), date(2026, 3, 31)),
        ("2026-04", date(2026, 4, 1), None),
    ]


def test_cycles_are_ordered_by_anchor_date_not_by_month_name():
    got = _cycles(
        [_salary("late", 5, 1), _salary("early", 3, 1)],
        [attribute_ruling("late", "2026-01"), attribute_ruling("early", "2026-09")],
    )
    assert [c.budget_month for c in got] == ["2026-09", "2026-01"]


def test_last_cycle_is_open():
    got = _cycles([_salary("a", 1, 5)], [attribute_ruling("a", "2026-01")])
    assert got == (Cycle("2026-01", date(2026, 1, 5), None),)
    assert cycle_of(got, date(2030, 1, 1)) == got[0]


def test_no_income_account_income_means_no_cycles():
    assert _cycles([tx("e1")], []) == ()


def test_row_on_start_day_belongs_to_new_cycle():
    ledger = build(
        _inputs(
            [_salary("a", 1, 10), _salary("b", 2, 10), tx("before", month=2, day=9), tx("on", month=2, day=10), tx("after", month=3, day=1)],
            [attribute_ruling("a", "2026-01"), attribute_ruling("b", "2026-02")],
        )
    )
    got = _by_id(ledger)
    assert got["before"].budget_month == "2026-01"
    assert got["on"].budget_month == "2026-02"
    assert got["after"].budget_month == "2026-02"
    assert got["a"].budget_month == "2026-01"
    assert got["b"].budget_month == "2026-02"


def test_row_on_last_day_of_closed_cycle_stays_in_it():
    ledger = build(
        _inputs([_salary("a", 1, 10), _salary("b", 2, 10), tx("last", month=2, day=9)], [attribute_ruling("a", "2026-01"), attribute_ruling("b", "2026-02")])
    )
    assert _by_id(ledger)["last"].budget_month == "2026-01"


def test_row_before_first_anchor_is_outside_cycle():
    ledger = build(_inputs([_salary("a", 1, 10), tx("old", day=9)], [attribute_ruling("a", "2026-01"), ruling("c1", "old", "food")]))
    old = _by_id(ledger)["old"]
    assert (old.status, old.budget_month, old.category) == ("outside_cycle", None, "food")


def test_row_on_first_anchor_day_is_inside():
    ledger = build(_inputs([_salary("a", 1, 10), tx("same", day=10)], [attribute_ruling("a", "2026-01")]))
    assert _by_id(ledger)["same"].budget_month == "2026-01"


def test_unresolved_before_first_anchor_is_outside_cycle():
    ledger = build(_inputs([_salary("a", 1, 10), tx("old", day=2), tx("new", day=11)], [attribute_ruling("a", "2026-01")]))
    got = _by_id(ledger)
    assert (got["old"].status, got["old"].budget_month) == ("outside_cycle", None)
    assert (got["new"].status, got["new"].budget_month) == ("unresolved", "2026-01")


def test_excluded_entry_has_no_budget_month():
    inp = _inputs([_salary("a", 1, 10), tx("off", account_id="off", day=15)], [attribute_ruling("a", "2026-01")], ACCOUNTS + (account("off", include=False),))
    off = _by_id(build(inp))["off"]
    assert (off.status, off.budget_month) == ("excluded", None)


def test_reimbursement_never_anchors():
    rulings = [attribute_ruling("a", "2026-01"), reimbursement_ruling("r", "food")]
    ledger = build(_inputs([_salary("a", 1, 10), _salary("r", 2, 1)], rulings))
    got = _by_id(ledger)
    assert got["r"].direction == "reimbursement"
    assert got["r"].budget_month == "2026-01"
    assert build_cycles(list(ledger), _inputs([_salary("a", 1, 10), _salary("r", 2, 1)], rulings)) == (Cycle("2026-01", date(2026, 1, 10), None),)


def test_income_on_spending_account_never_anchors():
    ledger = build(_inputs([_salary("a", 1, 10), tx("gift", direction="income", day=12), tx("gift2", direction="income", month=2, day=12)], [attribute_ruling("a", "2026-01")]))
    got = _by_id(ledger)
    assert got["gift"].budget_month == "2026-01" and got["gift2"].budget_month == "2026-01"


def test_expense_on_income_account_never_anchors():
    ledger = build(_inputs([_salary("a", 1, 10), tx("fee", account_id="inc", day=12)], [attribute_ruling("a", "2026-01")]))
    assert _by_id(ledger)["fee"].budget_month == "2026-01"


def test_unattributed_anchor_refuses():
    with pytest.raises(BuildRefused, match="has no attribute ruling") as refused:
        build(_inputs([_salary("a", 1, 10), _salary("b", 2, 10)], [attribute_ruling("a", "2026-01")]))
    assert refused.value.transaction_id == "b"


def test_attribute_on_non_candidate_refuses():
    with pytest.raises(BuildRefused, match="is not on an income-account income row") as refused:
        build(_inputs([_salary("a", 1, 10), tx("e1")], [attribute_ruling("a", "2026-01"), attribute_ruling("e1", "2026-01")]))
    assert refused.value.transaction_id == "e1"


def test_attribute_on_income_of_spending_account_refuses():
    with pytest.raises(BuildRefused, match="is not on an income-account income row"):
        build(_inputs([tx("gift", direction="income")], [attribute_ruling("gift", "2026-01")]))


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-1", "26-01", "", "2026-01-01", "x2026-01", "2026-01x", None])
def test_attribute_payload_malformed_refuses(month):
    with pytest.raises(BuildRefused, match="YYYY-MM") as refused:
        build(_inputs([_salary("a", 1, 10)], [attribute_ruling("a", month)]))
    assert refused.value.transaction_id == "a"


@pytest.mark.parametrize("month", ["2026-01", "2026-09", "2026-10", "2026-12"])
def test_attribute_payload_well_formed_builds(month):
    assert _cycles([_salary("a", 1, 10)], [attribute_ruling("a", month)])[0].budget_month == month


def test_attribution_decides_budget_month_not_category():
    ledger = build(
        _inputs(
            [_salary("a", 1, 10), tx("e1", day=12)],
            [attribute_ruling("a", "2026-03"), ruling("c1", "e1", "food")],
        )
    )
    got = _by_id(ledger)
    assert got["e1"].budget_month == "2026-03"
    assert got["e1"].category == "food"
    assert got["a"].category is None and got["a"].budget_month == "2026-03"


def test_no_cycles_leaves_entries_untouched():
    ledger = build(inputs(transactions=(tx("e1"),), rulings=(ruling("c1", "e1", "food"),)))
    assert (ledger[0].status, ledger[0].budget_month) == ("kept", None)
