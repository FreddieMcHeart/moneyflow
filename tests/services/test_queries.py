from datetime import date
from decimal import Decimal

from moneyflow.core.model import LedgerEntry
from moneyflow.core.reports import category_totals as core_totals
from moneyflow.services import queries
from moneyflow.storage import db
from tests.core._builders import account, owner, tx

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
    db.save_ledger(conn, ledger)
    return ledger


def test_spending_matches_core_totals(conn):
    ledger = _seed(conn)
    expected: dict[str, Decimal] = {}
    for (cat, _sub, cur), amount in core_totals(ledger, D1, D2).items():
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
