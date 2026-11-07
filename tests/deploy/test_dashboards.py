"""Dashboard definitions in deploy/metabase/dashboards: format, read-only SQL, and the SQL runs on synthetic rows."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db
from tests.core._builders import TODAY, account, attribute_ruling, owner, rule, tx

DIR = Path(__file__).resolve().parents[2] / "deploy" / "metabase" / "dashboards"
FILES = sorted(DIR.glob("*.json"))
CARD_KEYS = {"name", "sql", "display", "visualization_settings", "row", "col", "size_x", "size_y"}
FORBIDDEN = re.compile(r"\b(insert|update|delete|create|drop|alter)\b", re.IGNORECASE)
OPTIONAL = re.compile(r"\[\[.*?\]\]", re.DOTALL)  # Metabase drops an optional clause whose variable is unset


def _cards():
    return [(f.name, c) for f in FILES for c in json.loads(f.read_text())["cards"]]


def test_exactly_three_dashboards():
    assert [f.name for f in FILES] == ["categories-by-cycle.json", "month.json", "transactions.json"]
    assert [json.loads(f.read_text())["name"] for f in FILES] == ["Categories by cycle", "Month", "Transactions"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_format(path):
    data = json.loads(path.read_text())
    assert set(data) == {"name", "cards"} and isinstance(data["name"], str) and data["cards"]
    names = [c["name"] for c in data["cards"]]
    assert len(names) == len(set(names))
    for card in data["cards"]:
        assert set(card) == CARD_KEYS
        assert all(isinstance(card[k], str) and card[k] for k in ("name", "sql", "display"))
        assert isinstance(card["visualization_settings"], dict)
        assert all(isinstance(card[k], int) and card[k] >= 0 for k in ("row", "col", "size_x", "size_y"))


def test_sql_has_no_write_words():
    for _, card in _cards():
        assert not FORBIDDEN.search(card["sql"]), card["name"]


def test_guard_pattern_detects_writes():
    assert FORBIDDEN.search("DROP table x") and FORBIDDEN.search("select 1; Update t set a=1")
    assert not FORBIDDEN.search("select updated_at, created_by from t")


def _seed(conn):
    db.replace_imported(
        conn,
        owners=(owner(),),
        accounts=(account(), account("pay", role="income")),
        transactions=(
            tx("inc", account_id="pay", amount="1000.00", direction="income", description="salary", day=1),
            tx("t1", amount="40.00", description="cafe", day=2),
            tx("t2", amount="25.00", description="cinema", day=3),
            tx("t3", amount="7.50", description="mystery", day=4),
        ),
        rules=(
            rule("r1", 10, 0, "food", None, description_contains="cafe"),
            rule("r2", 10, 1, "fun", None, description_contains="cinema"),
            rule("r3", 10, 2, "salary", None, description_contains="salary"),
        ),
        rulings=(attribute_ruling("inc", "2026-01"),), categories=("food", "fun", "salary"), people=(), fx_rates=())
    rebuild(conn, today=TODAY)


def _run(conn, sql):
    sql = OPTIONAL.sub("", sql)
    assert "{{" not in sql
    with conn.transaction():
        conn.execute("set transaction read only")
        return conn.execute(sql).fetchall()


@pytest.mark.parametrize(("file", "card"), _cards(), ids=lambda v: v if isinstance(v, str) else v["name"])
def test_card_sql_runs_read_only_and_returns_rows(conn, file, card):
    _seed(conn)
    assert _run(conn, card["sql"])


def test_read_only_run_refuses_writes(conn):
    _seed(conn)
    with pytest.raises(Exception, match="read-only"):
        _run(conn, "insert into categories (name, created_by) values ('x', 'operator') returning name")


def _card(file, name):
    return next(c for f, c in _cards() if f == file and c["name"] == name)


def _total_actual(conn):
    return conn.execute("select sum(total_actual) as t from month_summaries where budget_month = '2026-01'").fetchone()["t"]


SPEND_CARDS = [("month.json", "Spent per category (UAH)"), ("categories-by-cycle.json", "Category by budget month (UAH)")]


@pytest.mark.parametrize(("file", "name"), SPEND_CARDS, ids=lambda v: v)
def test_spend_by_category_is_the_total_actual_population(conn, file, name):
    _seed(conn)
    rows = _run(conn, _card(file, name)["sql"])
    by_category = {r["category"]: r["amount_uah"] for r in rows if r.get("budget_month", "2026-01") == "2026-01"}
    assert by_category == {"food": Decimal("-40.00"), "fun": Decimal("-25.00"), "Uncategorized": Decimal("-7.50")}
    assert sum(by_category.values()) == _total_actual(conn) == Decimal("-72.50")


def test_total_by_budget_month_equals_total_actual(conn):
    _seed(conn)
    rows = _run(conn, _card("categories-by-cycle.json", "Total by budget month (UAH)")["sql"])
    assert rows == [{"budget_month": "2026-01", "amount_uah": _total_actual(conn)}]
    assert rows[0]["amount_uah"] == Decimal("-72.50")


def test_seed_has_a_kept_income_and_an_unresolved_expense(conn):
    _seed(conn)
    got = conn.execute("select direction, status, category from ledger_entries").fetchall()
    assert {"direction": "income", "status": "kept", "category": "salary"} in got
    assert {"direction": "expense", "status": "unresolved", "category": None} in got


def _bound(sql, month, category):
    return sql.replace("[[", "").replace("]]", "").replace("{{budget_month}}", f"'{month}'").replace("{{category}}", f"'{category}'")


def _transactions_sql():
    return next(c["sql"] for f, c in _cards() if f == "transactions.json")


def test_filters_bind_when_set(conn):
    _seed(conn)
    sql = _transactions_sql()
    rows = _run(conn, _bound(sql, "2026-01", "food"))
    assert [(r["status"], r["transaction_id"], r["category"]) for r in rows] == [("kept", "t1", "food")]
    assert _run(conn, _bound(sql, "1999-01", "food")) == []
    month_only = sql.replace("[[and category = {{category}}]]", "")
    assert {r["transaction_id"] for r in _run(conn, _bound(month_only, "2026-01", ""))} == {"inc", "t1", "t2", "t3"}


def test_broken_column_name_in_a_card_goes_red(conn):
    _seed(conn)
    sql = _card("month.json", "Spent per category (UAH)")["sql"].replace("amount_uah", "amount_uahh")
    with pytest.raises(Exception, match="amount_uahh"):
        _run(conn, sql)
