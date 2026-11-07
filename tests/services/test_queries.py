from datetime import date
from decimal import Decimal

import pytest

from moneyflow.core.model import BuildRefused, LedgerEntry
from moneyflow.core.reports import category_totals as core_totals
from moneyflow.services import queries
from moneyflow.storage import db
from tests.core._builders import account, attribute_ruling, owner, tx

D1, D2 = date(2026, 1, 1), date(2026, 1, 31)


def _entry(tid, category, subcategory, amount, currency="UAH", day=1, status="kept", account_id="acc1"):
    return LedgerEntry(tid, 0, date(2026, 1, day), account_id, category, subcategory, Decimal(amount),
                       currency, status, None, "rule:r", "expense", None, None, None, None, None)


def _seed(conn):
    db.replace_imported(
        conn, owners=(owner(),), accounts=(account(), account("acc_usd", "USD")),
        transactions=(
            tx("t1", description="Latte Shop", day=2), tx("t2", description="bus", day=3),
            tx("t3", description="x", day=4),
            tx("t4", account_id="acc_usd", amount="1.00", currency="USD", description="50%_off", day=5),
        ),
        rules=(), rulings=(), categories=("Cafe", "Transport"), people=(), fx_rates=())
    conn.execute("update transactions set comment = 'Big COFFEE' where id = 't3'")
    ledger = (
        _entry("t1", "Cafe", "Coffee", "-10.50", day=2), _entry("t2", "Transport", None, "-3.00", day=3),
        _entry("t3", "Cafe", None, "-4.25", day=4), _entry("t4", "Cafe", "Coffee", "-1.00", "USD", day=5, account_id="acc_usd"),
    )
    db.save_ledger(conn, ledger, summaries=(), built_for=date(2026, 1, 1))
    return ledger


def test_spending_matches_core_totals(conn):
    ledger = _seed(conn)
    expected: dict[str, Decimal] = {}
    for (cat, _sub, cur), amount in core_totals(ledger, start=D1, end=D2).items():
        if cat == "Cafe":
            expected[cur] = expected.get(cur, Decimal(0)) + amount
    assert queries.spending(conn, "Cafe", D1, D2) == expected
    assert expected == {"UAH": Decimal("-14.75"), "USD": Decimal("-1.00")}


def test_spending_unknown_category_returns_empty(conn):
    _seed(conn)
    assert queries.spending(conn, "Nope", D1, D2) == {}


def test_category_totals_rows(conn):
    _seed(conn)
    assert queries.category_totals(conn, D1, D2) == [
        ("Cafe", None, "UAH", Decimal("-4.25")),
        ("Cafe", "Coffee", "UAH", Decimal("-10.50")),
        ("Cafe", "Coffee", "USD", Decimal("-1.00")),
        ("Transport", None, "UAH", Decimal("-3.00")),
    ]


def test_find_transactions_text_and_limit(conn):
    _seed(conn)
    kw = dict(date_from=D1, date_to=D2, account_id=None)
    by_desc = queries.find_transactions(conn, text="latte", limit=10, **kw)
    assert [r["id"] for r in by_desc] == ["t1"]
    assert by_desc[0]["category"] == "Cafe" and by_desc[0]["amount"] == Decimal("-10.50")
    assert [r["id"] for r in queries.find_transactions(conn, text="coffee", limit=10, **kw)] == ["t3"]
    assert [r["id"] for r in queries.find_transactions(conn, text="%_", limit=10, **kw)] == ["t4"]
    assert len(queries.find_transactions(conn, text=None, limit=2, **kw)) == 2
    assert queries.find_transactions(conn, text=None, limit=10, date_from=D1, date_to=D2, account_id="zzz") == []
    assert [r["id"] for r in queries.find_transactions(
        conn, text=None, limit=10, date_from=date(2026, 1, 3), date_to=date(2026, 1, 3), account_id="acc1")] == ["t2"]


def _seed_cycle(conn):
    """One income-role account, one attributed salary on 2026-01-01 and one expense: a single open cycle 2026-01."""
    db.replace_imported(
        conn, owners=(owner(),), accounts=(account("inc", role="income"), account()),
        transactions=(tx("pay", account_id="inc", amount="1000.00", direction="income", day=1),
                      tx("t2", description="bus", day=5)),
        rules=(), rulings=(attribute_ruling("pay", "2026-01"),), categories=("Cafe", "Income"), people=(), fx_rates=())
    pay = LedgerEntry("pay", 0, date(2026, 1, 1), "inc", "Income", None, Decimal("1000.00"), "UAH", "kept", None,
                      "rule:r", "income", None, "2026-01", None, None, None)
    spend = LedgerEntry("t2", 0, date(2026, 1, 5), "acc1", "Cafe", None, Decimal("-40.00"), "UAH", "kept", None,
                        "rule:r", "expense", None, "2026-01", None, None, None)
    db.save_ledger(conn, (pay, spend), summaries=(), built_for=date(2026, 1, 1))


def test_month_summary_open_cycle_and_named_month(conn):
    _seed_cycle(conn)
    today = date(2026, 1, 15)
    summary = queries.month_summary(conn, None, today=today)
    assert queries.month_summary(conn, "2026-01", today=today) == summary
    assert (summary.budget_month, summary.cycle_open, summary.cycle_start) == ("2026-01", True, date(2026, 1, 1))
    assert dict(summary.income) == {"UAH": Decimal("1000.00")}
    assert dict(summary.total_actual) == {"UAH": Decimal("-40.00")}
    assert summary.cash_in_hand is None


def test_month_summary_unknown_month_refuses(conn):
    _seed_cycle(conn)
    with pytest.raises(BuildRefused, match="2026-07"):
        queries.month_summary(conn, "2026-07", today=date(2026, 1, 15))


def test_category_totals_by_budget_month(conn):
    _seed_cycle(conn)
    assert queries.category_totals(conn, budget_month="2026-01") == [
        ("Cafe", None, "UAH", Decimal("-40.00")), ("Income", None, "UAH", Decimal("1000.00"))]
    assert queries.category_totals(conn, budget_month="2026-02") == []
