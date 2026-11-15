"""Dashboard definitions in deploy/metabase/dashboards: format, read-only SQL, and the SQL runs on synthetic rows."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from moneyflow.core.model import PlanLine
from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db
from tests.core._builders import TODAY, account, attribute_ruling, owner, reimbursement_ruling, rule, tx

DIR = Path(__file__).resolve().parents[2] / "deploy" / "metabase" / "dashboards"
FILES = sorted(DIR.glob("*.json"))
CARD_KEYS = {"name", "sql", "display", "visualization_settings", "row", "col", "size_x", "size_y"}
FORBIDDEN = re.compile(r"\b(insert|update|delete|create|drop|alter)\b", re.IGNORECASE)
OPTIONAL = re.compile(r"\[\[.*?\]\]", re.DOTALL)  # Metabase drops an optional clause whose variable is unset


def _cards():
    return [(f.name, c) for f in FILES for c in json.loads(f.read_text())["cards"]]


def test_exactly_three_dashboards():
    assert [f.name for f in FILES] == ["months.json", "this-cycle.json", "transactions.json"]
    assert [json.loads(f.read_text())["name"] for f in FILES] == ["Months", "This cycle", "Transactions"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_format(path):
    data = json.loads(path.read_text())
    assert set(data) == {"name", "parameters", "cards"} and isinstance(data["name"], str) and data["cards"]
    assert isinstance(data["parameters"], list) and all(set(p) == {"name", "type"} for p in data["parameters"])
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




def _plan(month, category, amount, subcategory="", currency="UAH"):
    return PlanLine(
        id=f"{month}|{category}|{subcategory}|{currency}", budget_month=month, category=category,
        subcategory=subcategory, currency=currency, amount=Decimal(amount), source="template", handed_over=False,
    )


# Closed cycle 2026-01 (income "inc", day 1) and open cycle 2026-02 (income "inc2", day 20, attributed forward).
OPEN, CLOSED = "2026-02", "2026-01"


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
            tx("inc2", account_id="pay", amount="2000.00", direction="income", description="salary", day=20),
            tx("t4", amount="30.00", description="cafe", day=21),
            tx("t5", amount="10.00", description="cinema", day=22),
            tx("t6", amount="15.00", description="taxi", day=23),
            tx("t7", amount="5.00", description="mystery", day=24),
            tx("rb", amount="12.00", direction="income", description="refund", day=25),
        ),
        rules=(
            rule("r1", 10, 0, "food", None, description_contains="cafe"),
            rule("r2", 10, 1, "fun", None, description_contains="cinema"),
            rule("r3", 10, 2, "salary", None, description_contains="salary"),
            rule("r4", 10, 3, "transport", None, description_contains="taxi"),
        ),
        rulings=(attribute_ruling("inc", CLOSED), attribute_ruling("inc2", OPEN), reimbursement_ruling("rb", "food")),
        categories=("food", "fun", "salary", "transport", "rent"), people=(), fx_rates=(),
        plan_lines=(
            _plan(CLOSED, "food", "80.00"), _plan(CLOSED, "fun", "30.00"),
            _plan(OPEN, "food", "100.00"), _plan(OPEN, "fun", "50.00"), _plan(OPEN, "rent", "500.00"),
        ))
    rebuild(conn, today=TODAY)


def _run_cols(conn, sql):
    sql = OPTIONAL.sub("", sql)
    assert "{{" not in sql
    with conn.transaction():
        conn.execute("set transaction read only")
        cur = conn.execute(sql)
        return [d.name for d in cur.description], cur.fetchall()


def _run(conn, sql):
    return _run_cols(conn, sql)[1]


@pytest.mark.parametrize(("file", "card"), _cards(), ids=lambda v: v if isinstance(v, str) else v["name"])
def test_card_sql_runs_read_only_and_returns_rows(conn, file, card):
    _seed(conn)
    assert _run(conn, card["sql"])


def test_read_only_run_refuses_writes(conn):
    _seed(conn)
    with pytest.raises(Exception, match="read-only"):
        _run(conn, "insert into categories (name, created_by) values ('x', 'operator') returning name")


def _card(file, name):
    found = [c for f, c in _cards() if f == file and c["name"] == name]
    assert found, f"no card {name!r} in {file}"
    return found[0]


# (file, card, columns in order, money format per column)
DEC = {"number_style": "decimal", "decimals": 2}
UAH = {"number_style": "currency", "currency": "UAH"}
CONTRACT = [
    ("this-cycle.json", "This cycle", ["Category", "Subcategory", "Currency", "Plan", "Actual", "Transactions"],
     {"Plan": DEC, "Actual": DEC}),
    ("this-cycle.json", "Where the money went", ["Category", "Actual"], {"Actual": UAH}),
    ("months.json", "Overview", ["Month", "Income ₴", "Total plan ₴", "Spent ₴", "Balance ₴", "Days left", "Cycle open"],
     {"Income ₴": UAH, "Total plan ₴": UAH, "Spent ₴": UAH, "Balance ₴": UAH}),
    ("months.json", "History by month",
     ["Month", "Category", "Subcategory", "Currency", "Plan", "Actual", "Transactions"], {"Plan": DEC, "Actual": DEC}),
    ("transactions.json", "Transactions",
     ["Date", "Description", "Amount", "Currency", "Account", "Category", "Subcategory", "Status"], {"Amount": DEC}),
    ("transactions.json", "To review",
     ["Date", "Description", "Amount", "Currency", "Account", "MCC", "Category", "Subcategory"], {"Amount": DEC}),
]
IDS = [f"{f}:{n}" for f, n, _, _ in CONTRACT]


@pytest.mark.parametrize(("file", "name", "columns", "money"), CONTRACT, ids=IDS)
def test_column_titles_and_order(conn, file, name, columns, money):
    _seed(conn)
    assert _run_cols(conn, _card(file, name)["sql"])[0] == columns


@pytest.mark.parametrize(("file", "name", "columns", "money"), CONTRACT, ids=IDS)
def test_no_snake_case_header(conn, file, name, columns, money):
    _seed(conn)
    assert not [c for c in _run_cols(conn, _card(file, name)["sql"])[0] if re.fullmatch(r"[a-z0-9_]+", c)]


@pytest.mark.parametrize(("file", "name", "columns", "money"), CONTRACT, ids=IDS)
def test_money_columns_have_number_style(file, name, columns, money):
    settings = _card(file, name)["visualization_settings"].get("column_settings", {})
    assert settings == {json.dumps(["name", col], ensure_ascii=False, separators=(",", ":")): fmt for col, fmt in money.items()}


def test_display_and_layout():
    for dashboard in {f for f, *_ in CONTRACT}:
        cards = [c for f, c in _cards() if f == dashboard]
        row = 0
        for card in cards:
            pie = card["name"] == "Where the money went"
            assert card["display"] == ("pie" if pie else "table")
            assert (card["row"], card["col"], card["size_x"], card["size_y"]) == (row, 0, 24, 6 if pie else 8)
            assert card["visualization_settings"].get("pie.show_legend") is (True if pie else None)
            row += card["size_y"]
    assert [json.loads(f.read_text())["parameters"] for f in FILES if f.name != "transactions.json"] == [[], []]


def _this_cycle(conn):
    cols, rows = _run_cols(conn, _card("this-cycle.json", "This cycle")["sql"])
    return {(r["Category"], r["Subcategory"], r["Currency"]): r for r in rows}


def test_this_cycle_rows_are_the_union_of_plan_and_spending(conn):
    _seed(conn)
    got = _this_cycle(conn)
    assert set(got) == {("food", "", "UAH"), ("fun", "", "UAH"), ("rent", "", "UAH"), ("transport", "", "UAH")}
    rent = got[("rent", "", "UAH")]
    assert (rent["Plan"], rent["Actual"], rent["Transactions"]) == (Decimal("500.00"), None, 0)
    taxi = got[("transport", "", "UAH")]
    assert (taxi["Plan"], taxi["Actual"], taxi["Transactions"]) == (None, Decimal("15.00"), 1)
    food = got[("food", "", "UAH")]  # expense 30.00 minus the 12.00 reimbursement; both are kept rows
    assert (food["Plan"], food["Actual"], food["Transactions"]) == (Decimal("100.00"), Decimal("18.00"), 2)
    assert got[("fun", "", "UAH")]["Actual"] == Decimal("10.00")


@pytest.mark.parametrize(("file", "name"), [("this-cycle.json", "This cycle"), ("months.json", "History by month")])
def test_plan_with_empty_subcategory_and_spend_with_null_merge_into_one_row(conn, file, name):
    """plan_lines stores '' where ledger_entries stores null; the join must treat them as the same key."""
    _seed(conn)
    assert conn.execute("select count(*) as n from ledger_entries where category = 'food' and subcategory is null").fetchone()["n"]
    assert conn.execute("select count(*) as n from plan_lines where category = 'food' and subcategory = ''").fetchone()["n"]
    rows = _run(conn, _card(file, name)["sql"])
    food = [r for r in rows if r["Category"] == "food" and r["Plan"] is not None and r["Actual"] is not None]
    keys = [(r.get("Month"), r["Category"], r["Subcategory"], r["Currency"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert len(food) >= 1
    assert not [r for r in rows if r["Category"] == "food" and (r["Plan"] is None) != (r["Actual"] is None)]


def test_actual_matches_total_actual(conn):
    """month_summaries.total_actual also counts the unresolved expense (it has no category, so the card cannot show it);
    the card must equal total_actual over the kept population only."""
    _seed(conn)
    summary = conn.execute("select total_actual from month_summaries where budget_month = %s and currency = 'UAH'", (OPEN,)).fetchone()
    unresolved = conn.execute(
        "select coalesce(sum(amount), 0) as s from ledger_entries where budget_month = %s and status = 'unresolved'", (OPEN,)
    ).fetchone()["s"]
    card_sum = sum(r["Actual"] for r in _this_cycle(conn).values() if r["Actual"] is not None)
    assert unresolved == Decimal("-5.00")
    assert card_sum == -(summary["total_actual"] - unresolved) == Decimal("43.00")


def test_where_the_money_went_is_open_cycle_uah_by_category(conn):
    _seed(conn)
    rows = _run(conn, _card("this-cycle.json", "Where the money went")["sql"])
    assert {r["Category"]: r["Actual"] for r in rows} == {"food": Decimal("18.00"), "transport": Decimal("15.00"), "fun": Decimal("10.00")}


def test_history_has_no_open_month(conn):
    _seed(conn)
    rows = _run(conn, _card("months.json", "History by month")["sql"])
    assert {r["Month"] for r in rows} == {CLOSED}
    closed = {(r["Category"], r["Subcategory"]): r for r in rows}
    assert closed[("food", "")]["Plan"] == Decimal("80.00") and closed[("food", "")]["Actual"] == Decimal("40.00")


def test_to_review_rows_are_all_unresolved(conn):
    _seed(conn)
    rows = _run(conn, _card("transactions.json", "To review")["sql"])
    unresolved = conn.execute("select count(*) as n from ledger_entries where status = 'unresolved'").fetchone()["n"]
    assert len(rows) == unresolved == 2
    assert {r["Description"] for r in rows} == {"mystery"}
    assert all(r["Category"] is None for r in rows)


def test_overview_balance_is_income_minus_spent_and_total_plan_is_summed_plan(conn):
    _seed(conn)
    rows = {r["Month"]: r for r in _run(conn, _card("months.json", "Overview")["sql"])}
    assert set(rows) == {CLOSED, OPEN}
    for r in rows.values():
        assert r["Balance ₴"] == r["Income ₴"] - r["Spent ₴"]
    assert rows[OPEN]["Total plan ₴"] == Decimal("650.00") and rows[CLOSED]["Total plan ₴"] == Decimal("110.00")
    assert rows[OPEN]["Cycle open"] is True and rows[CLOSED]["Cycle open"] is False
    assert rows[OPEN]["Income ₴"] == Decimal("2000.0")


def _sorted_by(rows, key, reverse=False):
    return rows == sorted(rows, key=key, reverse=reverse)


def test_sort_order(conn):
    _seed(conn)
    run = lambda f, n: _run(conn, _card(f, n)["sql"])  # noqa: E731
    actuals = [r["Actual"] for r in run("this-cycle.json", "This cycle")]
    assert actuals == [Decimal("18.00"), Decimal("15.00"), Decimal("10.00"), None]
    assert [r["Actual"] for r in run("this-cycle.json", "Where the money went")] == [Decimal("18.00"), Decimal("15.00"), Decimal("10.00")]
    assert [r["Month"] for r in run("months.json", "Overview")] == [OPEN, CLOSED]
    history = [(r["Month"], r["Category"], r["Subcategory"]) for r in run("months.json", "History by month")]
    assert history == sorted(history) and len(history) > 1
    for name in ("Transactions", "To review"):
        dates = [r["Date"] for r in run("transactions.json", name)]
        assert dates == sorted(dates, reverse=True) and len(dates) > 1


def test_seed_has_a_kept_income_an_unresolved_expense_and_a_reimbursement(conn):
    _seed(conn)
    got = conn.execute("select direction, status, category from ledger_entries").fetchall()
    assert {"direction": "income", "status": "kept", "category": "salary"} in got
    assert {"direction": "expense", "status": "unresolved", "category": None} in got
    assert {"direction": "reimbursement", "status": "kept", "category": "food"} in got


def _bound(sql, month, category):
    return sql.replace("[[", "").replace("]]", "").replace("{{budget_month}}", f"'{month}'").replace("{{category}}", f"'{category}'")


def _transactions_sql():
    return _card("transactions.json", "Transactions")["sql"]


def test_filters_bind_when_set(conn):
    _seed(conn)
    sql = _transactions_sql()
    rows = _run(conn, _bound(sql, CLOSED, "food"))
    assert [(r["Status"], r["Description"], r["Category"], r["Account"]) for r in rows] == [("kept", "cafe", "food", "acc1")]
    assert _run(conn, _bound(sql, "1999-01", "food")) == []
    month_only = sql.replace("[[and le.category = {{category}}]]", "")
    assert {r["Description"] for r in _run(conn, _bound(month_only, CLOSED, ""))} == {"salary", "cafe", "cinema", "mystery"}


def test_broken_column_name_in_a_card_goes_red(conn):
    _seed(conn)
    sql = _card("months.json", "Overview")["sql"].replace("days_left", "days_leftt")
    with pytest.raises(Exception, match="days_leftt"):
        _run(conn, sql)
