"""End to end on a synthetic old repo: import_old.run -> rebuild -> queries -> one MCP tool. No real data."""

from datetime import date
from decimal import Decimal

from moneyflow.services import queries
from moneyflow.services.rebuild import rebuild
from tests.api.test_mcp_server import _call, _payload
from tests.core._builders import TODAY
from tests.tools.test_import_old import _HEADER, _csv, _old_repo
from tools.import_old.__main__ import run

D1, D2 = date(2026, 1, 1), date(2026, 1, 31)


def _row(tid, amount, currency, direction, description, day):
    return f"2026-01-{day:02d},2026-01-{day:02d}T12:00:00+02:00,{amount},{currency},{direction},5814,{description},,,{tid}"


def _repo(tmp_path):
    repo = _old_repo(
        tmp_path,
        accounts=(("uah", "UAH", True), ("usd", "USD", True)),
        rules=[
            {"name": "coffee", "priority": 10, "match": {"description_contains": "cafe"},
             "category": "food", "subcategory": "Coffee"},
            {"name": "shop", "priority": 20, "match": {"description_contains": "shop"}, "category": "food"},
        ],
        overrides={"u3": {"category": "misc", "note": "gift"}},
        files={
            "uah_2026-01-01_2026-01-31.csv": _csv(
                _row("u1", "120.50", "UAH", "expense", "cafe one", 3),
                _row("u2", "40.00", "UAH", "expense", "shop", 4),
                _row("u3", "75.00", "UAH", "expense", "shop", 5),
                _row("u4", "1000.00", "UAH", "income", "unknown payer", 6),
                header=_HEADER,
            ),
            "usd_2026-01-01_2026-01-31.csv": _csv(
                _row("d1", "3.25", "USD", "expense", "cafe abroad", 7), header=_HEADER
            ),
        },
        fx_rows=("e2efx0000001,,2026-01-07,41.0000,official,test,,,",),
    )
    return repo


def test_import_rebuild_query_mcp(conn, db_url, tmp_path):
    counts = run(_repo(tmp_path), conn)
    assert counts == {"owners": 1, "accounts": 3, "transactions": 5, "rules": 14, "rulings": 1, "categories": 2, "people": 0}

    ledger = rebuild(conn, today=TODAY)
    assert sorted((e.transaction_id, e.status, e.category, e.subcategory, e.amount, e.currency) for e in ledger) == [
        ("d1", "kept", "food", "Coffee", Decimal("-3.25"), "USD"),
        ("u1", "kept", "food", "Coffee", Decimal("-120.50"), "UAH"),
        ("u2", "kept", "food", None, Decimal("-40.00"), "UAH"),
        ("u3", "kept", "misc", None, Decimal("-75.00"), "UAH"),
        ("u4", "unresolved", None, None, Decimal("1000.00"), "UAH"),
    ]

    assert queries.spending(conn, "food", D1, D2) == {"UAH": Decimal("-160.50"), "USD": Decimal("-3.25")}
    assert queries.category_totals(conn, D1, D2) == [
        ("food", None, "UAH", Decimal("-40.00")),
        ("food", "Coffee", "UAH", Decimal("-120.50")),
        ("food", "Coffee", "USD", Decimal("-3.25")),
        ("misc", None, "UAH", Decimal("-75.00")),
    ]

    out = _payload(_call(db_url, "spending", {"category": "food", "date_from": "2026-01-01", "date_to": "2026-01-31"}))
    assert out == {"UAH": "-160.50", "USD": "-3.25"}
