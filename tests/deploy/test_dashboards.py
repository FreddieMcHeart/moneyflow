"""Dashboard definitions in deploy/metabase/dashboards: format, read-only SQL, and the SQL runs on synthetic rows."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from moneyflow.core.model import PlanLine
from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db
from tests.core._builders import TODAY, account, attribute_ruling, fx_rate, owner, reimbursement_ruling, rule, tx

DIR = Path(__file__).resolve().parents[2] / "deploy" / "metabase" / "dashboards"
FILES = sorted(DIR.glob("*.json"))
LAYOUT_KEYS = {"row", "col", "size_x", "size_y"}
CARD_KEYS = {"sql": {"name", "sql", "display", "visualization_settings"},
             "pivot": {"name", "pivot", "display", "visualization_settings"}}  # plus LAYOUT_KEYS on a dashboard; on_dashboard, filters optional
FORBIDDEN = re.compile(r"\b(insert|update|delete|create|drop|alter)\b", re.IGNORECASE)
OPTIONAL = re.compile(r"\[\[.*?\]\]", re.DOTALL)  # Metabase drops an optional clause whose variable is unset


def _cards():
    return [(f.name, c) for f in FILES for c in json.loads(f.read_text())["cards"]]


def _sql_cards():
    return [(f, c) for f, c in _cards() if "sql" in c]


def _on_dashboard(card):
    return card.get("on_dashboard", True)


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
        kinds = [k for k in CARD_KEYS if k in card]
        assert len(kinds) == 1, card["name"]  # exactly one of sql or pivot
        expected = CARD_KEYS[kinds[0]] | (LAYOUT_KEYS if _on_dashboard(card) else {"on_dashboard"})
        optional = {"on_dashboard", "filters"} if kinds[0] == "pivot" else {"on_dashboard"}
        assert set(card) - optional == expected - {"on_dashboard"} and ("on_dashboard" not in card or card["on_dashboard"] is False)
        if "filters" in card:
            assert card["filters"] and all(isinstance(k, str) and isinstance(v, str) for k, v in card["filters"].items())
        assert all(isinstance(card[k], str) and card[k] for k in ("name", "display"))
        assert isinstance(card["visualization_settings"], dict)
        if _on_dashboard(card):
            assert all(isinstance(card[k], int) and card[k] >= 0 for k in LAYOUT_KEYS)
        if "sql" in card:
            assert isinstance(card["sql"], str) and card["sql"]


def test_sql_has_no_write_words():
    for _, card in _sql_cards():
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
FUTURE = "2026-03"  # the month after the open cycle: plan lines only


def _seed(conn, extra_plan=(), usd_income=False, future=True):
    db.replace_imported(
        conn,
        owners=(owner(),),
        accounts=(account(), account("pay", role="income"), account("usd", "USD", role="income")),
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
            *(tx("usd1", account_id="usd", amount="10.00", currency="USD", direction="income", description="salary", day=26),)
            * usd_income,
        ),
        rules=(
            rule("r1", 10, 0, "food", None, description_contains="cafe"),
            rule("r2", 10, 1, "fun", None, description_contains="cinema"),
            rule("r3", 10, 2, "salary", None, description_contains="salary"),
            rule("r4", 10, 3, "transport", None, description_contains="taxi"),
        ),
        rulings=(attribute_ruling("inc", CLOSED), attribute_ruling("inc2", OPEN), reimbursement_ruling("rb", "food"),
                 *((attribute_ruling("usd1", OPEN),) * usd_income)),
        categories=("food", "fun", "salary", "transport", "rent"), people=(), fx_rates=(fx_rate(26, "41.6000", "sell"), fx_rate(26, "41.3000", "official")) if usd_income else (),
        plan_lines=(
            _plan(CLOSED, "food", "80.00"), _plan(CLOSED, "fun", "30.00"),
            _plan(OPEN, "food", "100.00"), _plan(OPEN, "fun", "50.00"), _plan(OPEN, "rent", "500.00"), *((_plan(FUTURE, "food", "120.00"),) * future), *extra_plan,
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


@pytest.mark.parametrize(("file", "card"), _sql_cards(), ids=lambda v: v if isinstance(v, str) else v["name"])
def test_card_sql_runs_read_only_and_returns_rows(conn, file, card):
    _seed(conn)
    assert _run(conn, card["sql"])


def test_read_only_run_refuses_writes(conn):
    _seed(conn)
    with pytest.raises(Exception, match="read-only"):
        _run(conn, "insert into categories (name, created_by) values ('x', 'operator') returning name")


def _card(file, name):
    found = [c for f, c in _sql_cards() if f == file and c["name"] == name]
    assert found, f"no card {name!r} in {file}"
    return found[0]


# (file, card, columns in order, money format per column)
DEC = {"number_style": "decimal", "decimals": 0}
UAH = {"number_style": "currency", "currency": "UAH", "currency_style": "symbol", "currency_in_header": False, "decimals": 0}
CONTRACT = [
    ("this-cycle.json", "This cycle (rows)", ["Category", "Subcategory", "Currency", "Plan", "Actual", "Transactions"],
     {"Plan": DEC, "Actual": DEC}),
    ("this-cycle.json", "Plan vs Actual", ["Category", "Plan", "Actual"], {"Plan": UAH, "Actual": UAH}),
    ("this-cycle.json", "Where the money went", ["Category", "Actual"], {"Actual": UAH}),
    ("months.json", "Overview", ["Month", "Income ₴", "Total plan ₴", "Spent ₴", "Balance ₴", "Days left", "Cycle open"],
     {"Income ₴": UAH, "Total plan ₴": UAH, "Spent ₴": UAH, "Balance ₴": UAH}),
    ("months.json", "History by month (rows)",
     ["Month", "Category", "Subcategory", "Currency", "Plan", "Actual", "Transactions"], {"Plan": DEC, "Actual": DEC}),
    ("months.json", "Plan ahead", ["Month", "Category", "Subcategory", "Currency", "Plan"], {"Plan": DEC}),
    ("transactions.json", "Transactions (rows)",
     ["Date", "Month", "Description", "Amount", "Currency", "Account", "Category", "Subcategory", "Count"], {"Amount": DEC}),
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
            if not _on_dashboard(card):
                assert card["display"] == "table" and not LAYOUT_KEYS & set(card), card["name"]
                continue
            display = {"Where the money went": "pie", "Plan vs Actual": "row"}.get(card["name"], "pivot" if "pivot" in card else "table")
            size_y = {"Plan vs Actual": 10, "By category": 8, "Where the money went": 6}.get(card["name"], 8)
            assert card["display"] == display, card["name"]
            assert (card["row"], card["col"], card["size_x"], card["size_y"]) == (row, 0, 24, size_y), card["name"]
            assert card["visualization_settings"].get("pie.show_legend") is (True if display == "pie" else None)
            assert card["visualization_settings"].get("pie.slice_threshold") == (0 if display == "pie" else None), card["name"]
            row += card["size_y"]
    assert [json.loads(f.read_text())["parameters"] for f in FILES if f.name != "transactions.json"] == [[], []]


def _this_cycle_file():
    return json.loads((DIR / "this-cycle.json").read_text())


def _this_cycle_card(name):
    found = [c for c in _this_cycle_file()["cards"] if c["name"] == name]
    assert len(found) == 1, name
    return found[0]


def test_this_cycle_is_a_pivot_over_its_rows_card():
    cards = _this_cycle_file()["cards"]
    assert [(c["name"], _on_dashboard(c)) for c in cards] == [
        ("This cycle (rows)", False), ("Plan vs Actual", True), ("By category", True), ("Where the money went", True)]
    rows, pivot, pie = (_this_cycle_card(n) for n in ("This cycle (rows)", "By category", "Where the money went"))
    assert "sql" in rows and "pivot" not in rows and rows["display"] == "table"
    assert "pivot" in pivot and "sql" not in pivot and pivot["display"] == "pivot"
    assert pivot["pivot"] == {"source": "This cycle (rows)", "rows": ["Category", "Subcategory"], "columns": ["Currency"],
                              "values": ["Plan", "Actual", "Transactions"]}
    assert (pivot["row"], pivot["size_y"], pie["row"], pie["size_y"]) == (10, 8, 18, 6)


def test_this_cycle_pivot_settings_set_titles_formats_and_no_row_totals():
    pivot = _this_cycle_card("By category")
    settings = pivot["visualization_settings"]
    assert settings["pivot.show_row_totals"] is False  # no total crosses currencies
    assert "pivot_table.column_split" not in settings  # the provisioner builds it
    assert settings["pivot_table.collapsed_rows"] == {"value": ["1"], "rows": ["Category", "Subcategory"]}
    assert settings["column_settings"] == {
        '["name","sum"]': {"column_title": "Plan", **DEC},
        '["name","sum_2"]': {"column_title": "Actual", **DEC},
        '["name","sum_3"]': {"column_title": "Transactions"},
    }
    assert [v for v in pivot["pivot"]["values"]] == [CONTRACT[0][2][i] for i in (3, 4, 5)]  # titles match the rows card's columns


def test_plan_vs_actual_is_a_row_chart_of_plan_and_actual_by_category():
    card = _this_cycle_card("Plan vs Actual")
    settings = card["visualization_settings"]
    assert settings["graph.dimensions"] == ["Category"] and settings["graph.metrics"] == ["Plan", "Actual"]
    assert card["display"] == "row" and _on_dashboard(card)


def _this_cycle(conn):
    cols, rows = _run_cols(conn, _card("this-cycle.json", "This cycle (rows)")["sql"])
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


@pytest.mark.parametrize(("file", "name"), [("this-cycle.json", "This cycle (rows)"), ("months.json", "History by month (rows)")])
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
    rows = _run(conn, _card("months.json", "History by month (rows)")["sql"])
    assert {r["Month"] for r in rows} == {CLOSED}
    assert FUTURE not in {r["Month"] for r in rows}
    closed = {(r["Category"], r["Subcategory"]): r for r in rows}
    assert closed[("food", "")]["Plan"] == Decimal("80.00") and closed[("food", "")]["Actual"] == Decimal("40.00")


def _plan_ahead(conn):
    return _run(conn, _card("months.json", "Plan ahead")["sql"])


def test_plan_ahead_holds_only_months_after_the_open_cycle_one_row_per_key(conn):
    _seed(conn, extra_plan=(_plan(FUTURE, "food", "5.00", subcategory="cafe"), _plan(FUTURE, "rent", "20.00", currency="USD"),
                            _plan(FUTURE, "fun", "7.00", subcategory="games")))
    rows = _plan_ahead(conn)
    assert {r["Month"] for r in rows} == {FUTURE}
    got = {(r["Category"], r["Subcategory"], r["Currency"]): r["Plan"] for r in rows}
    # one row per (category, subcategory, currency); USD stays apart from UAH
    assert got == {("food", "", "UAH"): Decimal("120.00"), ("food", "cafe", "UAH"): Decimal("5.00"), ("rent", "", "USD"): Decimal("20.00"),
                   ("fun", "games", "UAH"): Decimal("7.00")}


def test_history_and_plan_ahead_month_sets_are_disjoint(conn):
    _seed(conn)
    history = {r["Month"] for r in _run(conn, _card("months.json", "History by month (rows)")["sql"])}
    ahead = {r["Month"] for r in _plan_ahead(conn)}
    assert history == {CLOSED} and ahead == {FUTURE} and not history & ahead


def test_plan_ahead_is_empty_when_no_plan_month_follows_the_open_cycle(conn):
    _seed(conn, future=False)
    assert _plan_ahead(conn) == []


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


def test_overview_income_counts_usd_income_at_the_official_rate(conn):
    _seed(conn, usd_income=True)
    rows = {r["Month"]: r for r in _run(conn, _card("months.json", "Overview")["sql"])}
    assert set(rows) == {CLOSED, OPEN}
    assert rows[OPEN]["Income ₴"] == Decimal("2410.0")  # 2000.0 UAH + 10.00 USD at 41.30 floored to 5 = 410.00
    assert rows[OPEN]["Balance ₴"] == rows[OPEN]["Income ₴"] - rows[OPEN]["Spent ₴"]


def _sorted_by(rows, key, reverse=False):
    return rows == sorted(rows, key=key, reverse=reverse)


def test_sort_order(conn):
    _seed(conn)
    run = lambda f, n: _run(conn, _card(f, n)["sql"])  # noqa: E731
    actuals = [r["Actual"] for r in run("this-cycle.json", "This cycle (rows)")]
    assert actuals == [Decimal("18.00"), Decimal("15.00"), Decimal("10.00"), None]
    assert [r["Actual"] for r in run("this-cycle.json", "Where the money went")] == [Decimal("18.00"), Decimal("15.00"), Decimal("10.00")]
    assert [r["Month"] for r in run("months.json", "Overview")] == [OPEN, CLOSED]
    history = [(r["Month"], r["Category"], r["Subcategory"]) for r in run("months.json", "History by month (rows)")]
    assert history == sorted(history) and len(history) > 1
    for name in ("Transactions (rows)", "To review"):
        dates = [r["Date"] for r in run("transactions.json", name)]
        assert dates == sorted(dates, reverse=True) and len(dates) > 1


def test_seed_has_a_kept_income_an_unresolved_expense_and_a_reimbursement(conn):
    _seed(conn)
    got = conn.execute("select direction, status, category from ledger_entries").fetchall()
    assert {"direction": "income", "status": "kept", "category": "salary"} in got
    assert {"direction": "expense", "status": "unresolved", "category": None} in got
    assert {"direction": "reimbursement", "status": "kept", "category": "food"} in got


def test_broken_column_name_in_a_card_goes_red(conn):
    _seed(conn)
    sql = _card("months.json", "Overview")["sql"].replace("days_left", "days_leftt")
    with pytest.raises(Exception, match="days_leftt"):
        _run(conn, sql)


def test_mixed_currency_category_gives_one_rows_card_row_per_currency_and_the_pivot_splits_currency_by_column(conn):
    """The data side of "no total crosses currencies": a fabricated category with a UAH and a USD key. The pivot totals
    themselves are checked live (spec section 6, After T5)."""
    from tools.metabase.api import _card_body

    _seed(conn, extra_plan=(_plan(OPEN, "rent", "20.00", currency="USD"),))
    cols, rows = _run_cols(conn, _card("this-cycle.json", "This cycle (rows)")["sql"])
    rent = {r["Currency"]: r for r in rows if r["Category"] == "rent"}
    assert set(rent) == {"UAH", "USD"} and len([r for r in rows if r["Category"] == "rent"]) == 2
    assert (rent["UAH"]["Plan"], rent["USD"]["Plan"]) == (Decimal("500.00"), Decimal("20.00"))
    # the shape the provisioner sends: Currency is a column split, never a row, and no row totals are shown
    pivot = _this_cycle_card("By category")
    metadata = [{"name": c, "base_type": "type/Text"} for c in cols]
    body = _card_body(pivot, 1, 2, None, source=(9, metadata))
    split = body["visualization_settings"]["pivot_table.column_split"]
    assert split["columns"] == ["Currency"] and "Currency" not in split["rows"]
    assert body["visualization_settings"]["pivot.show_row_totals"] is False
    # computed subtotals per (Category, Currency): the mixed category has two, and none adds UAH to USD
    totals = {}
    for r in rows:
        totals[(r["Category"], r["Currency"])] = totals.get((r["Category"], r["Currency"]), Decimal(0)) + (r["Plan"] or 0)
    assert totals[("rent", "UAH")] == Decimal("500.00") and totals[("rent", "USD")] == Decimal("20.00")


def _plan_vs_actual(conn):
    return {r["Category"]: (r["Plan"], r["Actual"]) for r in _run(conn, _card("this-cycle.json", "Plan vs Actual")["sql"])}


def _uah_rows_summed_by_category(conn):
    sums = {}
    for (category, _, currency), r in _this_cycle(conn).items():
        if currency != "UAH":
            continue
        plan, actual = sums.get(category, (Decimal(0), Decimal(0)))
        sums[category] = (plan + (r["Plan"] or 0), actual + (r["Actual"] or 0))
    return sums


def test_plan_vs_actual_equals_the_uah_rows_summed_by_category(conn):
    _seed(conn)
    got = _plan_vs_actual(conn)
    assert got == _uah_rows_summed_by_category(conn)
    assert got == {"food": (Decimal("100.00"), Decimal("18.00")), "fun": (Decimal("50.00"), Decimal("10.00")),
                   "rent": (Decimal("500.00"), Decimal("0")), "transport": (Decimal("0"), Decimal("15.00"))}


def test_plan_vs_actual_does_not_show_usd(conn):
    _seed(conn, extra_plan=(_plan(OPEN, "rent", "20.00", currency="USD"), _plan(OPEN, "travel", "70.00", currency="USD")))
    got = _plan_vs_actual(conn)
    assert got == _uah_rows_summed_by_category(conn)
    assert got["rent"] == (Decimal("500.00"), Decimal("0")) and "travel" not in got


# ---- T7: History by month and Transactions grouped (fabricated seed only) ----

def _file(name):
    return json.loads((DIR / name).read_text())


def _named(file, name):
    found = [c for c in _file(file)["cards"] if c["name"] == name]
    assert len(found) == 1, (file, name)
    return found[0]


def test_months_and_transactions_card_order_and_visibility():
    assert [(c["name"], _on_dashboard(c)) for c in _file("months.json")["cards"]] == [
        ("Overview", True), ("History by month (rows)", False), ("History by month", True),
        ("Plan ahead", True)]
    assert [(c["name"], _on_dashboard(c)) for c in _file("transactions.json")["cards"]] == [
        ("Transactions (rows)", False), ("Transactions by category", True), ("To review", True)]


def test_history_by_month_is_a_pivot_collapsed_to_month_with_no_row_totals():
    card = _named("months.json", "History by month")
    assert card["pivot"] == {"source": "History by month (rows)", "rows": ["Month", "Category", "Subcategory"],
                             "columns": ["Currency"], "values": ["Plan", "Actual", "Transactions"]}
    settings = card["visualization_settings"]
    assert card["display"] == "pivot" and "filters" not in card
    assert settings["pivot.show_row_totals"] is False  # no total crosses currencies
    assert settings["pivot_table.collapsed_rows"] == {"value": ["1"], "rows": ["Month", "Category", "Subcategory"]}
    assert "pivot_table.column_split" not in settings
    assert settings["column_settings"] == {
        '["name","sum"]': {"column_title": "Plan", **DEC},
        '["name","sum_2"]': {"column_title": "Actual", **DEC},
        '["name","sum_3"]': {"column_title": "Transactions"},
    }


def test_transactions_by_category_is_a_pivot_with_amount_and_count_and_two_filters():
    card = _named("transactions.json", "Transactions by category")
    assert card["pivot"] == {"source": "Transactions (rows)", "rows": ["Category", "Subcategory", "Date", "Description", "Account"],
                             "columns": ["Currency"], "values": ["Amount", "Count"]}
    assert card["filters"] == {"budget_month": "Month", "category": "Category"}
    settings = card["visualization_settings"]
    assert card["display"] == "pivot" and "pivot_table.column_split" not in settings
    assert settings["column_settings"] == {'["name","sum"]': {"column_title": "Amount", **DEC},
                                           '["name","sum_2"]': {"column_title": "Count"}}  # Count has no number format
    assert settings["pivot.show_row_totals"] is False  # Amount must not be added across currencies


def test_no_dashboard_card_carries_a_template_tag():
    """The filters are pivot field mappings now; a `{{...}}` left in a card would be a dead variable."""
    assert not [(f, c["name"]) for f, c in _sql_cards() if "{{" in c["sql"] or "[[" in c["sql"]]


def test_every_pivot_filter_names_a_declared_parameter_and_a_source_column(conn):
    _seed(conn)
    checked = 0
    for file in FILES:
        definition = json.loads(file.read_text())
        declared = {p["name"] for p in definition["parameters"]}
        sources = {c["name"]: c for c in definition["cards"] if "sql" in c}
        pivots = [c for c in definition["cards"] if "pivot" in c]
        for pivot in pivots:
            columns = _run_cols(conn, sources[pivot["pivot"]["source"]]["sql"])[0]
            assert set(pivot.get("filters", {})) == declared, pivot["name"]  # every parameter is mapped, none invented
            for column in pivot.get("filters", {}).values():
                assert column in columns, (pivot["name"], column)
                checked += 1
    assert checked == 2


def _transactions_rows(conn):
    return _run_cols(conn, _card("transactions.json", "Transactions (rows)")["sql"])[1]


def test_transactions_rows_hold_kept_rows_only_without_a_limit(conn):
    _seed(conn)
    sql = _card("transactions.json", "Transactions (rows)")["sql"]
    assert "limit" not in sql.lower() and "where le.status = 'kept'" in sql and "status" not in sql.split(" from ")[0].lower()
    rows = _transactions_rows(conn)
    kept = conn.execute("select count(*) as n from ledger_entries where status = 'kept'").fetchone()["n"]
    total = conn.execute("select count(*) as n from ledger_entries").fetchone()["n"]
    assert len(rows) == kept and kept < total  # the seed holds unresolved rows that must not appear
    assert "mystery" not in {r["Description"] for r in rows}
    assert {r["Month"] for r in rows} == {CLOSED, OPEN}


def test_transactions_count_column_is_one_per_row_so_a_merged_leaf_counts_its_rows(conn):
    _seed(conn)
    rows = _transactions_rows(conn)
    assert rows and {r["Count"] for r in rows} == {1}
    by_key = {}
    for r in rows:  # what the pivot merges: equal row keys and currency; Amount and Count are summed
        key = (r["Category"], r["Subcategory"], r["Date"], r["Description"], r["Account"], r["Currency"])
        by_key[key] = (by_key.get(key, (Decimal(0), 0))[0] + r["Amount"], by_key.get(key, (Decimal(0), 0))[1] + r["Count"])
    assert sum(count for _, count in by_key.values()) == len(rows)
    assert sum(amount for amount, _ in by_key.values()) == sum(r["Amount"] for r in rows)
