"""Task 2 (phase 0c): the import writes only what changed, counts per table, operator rows untouched."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from moneyflow.core.model import FxRate, PlanLine
from moneyflow.storage import db
from tests.core._builders import account, owner, person, rule, ruling, tx

_KEYS = {
    "owners": "id", "accounts": "id", "transactions": "id", "rules": "id", "rulings": "id",
    "categories": "name", "people": "id", "fx_rates": "id", "plan_lines": "id",
}
_ZERO = (0, 0, 0)


def _plan(category: str, subcategory: str = "", amount: str = "100.00") -> PlanLine:
    return PlanLine(
        id=f"2026-01|{category}|{subcategory}|UAH", budget_month="2026-01", category=category,
        subcategory=subcategory, currency="UAH", amount=Decimal(amount), source="template", handed_over=False,
    )


def _import_rule(id: str, **match):
    return replace(rule(id, 10, 0, "food", "cafe", **match), created_by="import")


def _import_ruling(id: str, tid: str, category: str = "food"):
    return replace(ruling(id, tid, category), created_by="import")


def _input(**over):
    base = dict(
        owners=(owner(),),
        accounts=(account(),),
        transactions=(tx("t1", amount="10.00"), tx("t2", amount="20.00")),
        rules=(_import_rule("r1", description_contains="shop"),),
        rulings=(_import_ruling("g1", "t1"),),
        categories=("food", "misc"),
        people=(person("alice", "Alice", "12.50", "n"),),
        fx_rates=(FxRate("x1", date(2026, 1, 5), Decimal("41.2500"), "sell", "bank"),),
        plan_lines=(_plan("food"), _plan("misc", "x")),
    )
    base.update(over)
    return base


def _write(conn, **over):
    return db.replace_imported(conn, **_input(**over))


def _xmins(conn) -> dict:
    return {
        (t, r["k"]): r["x"]
        for t, key in _KEYS.items()
        for r in conn.execute(f"select {key} as k, xmin::text as x from {t}").fetchall()  # type: ignore[arg-type]
    }


def _iud(counts, table):
    return tuple(counts[table][:3])


def test_second_import_of_same_input_writes_nothing(conn):
    first = _write(conn)
    assert all(first[t][0] > 0 for t in ("owners", "accounts", "transactions", "rules", "rulings", "categories",
                                         "people", "fx_rates", "plan_lines"))
    before = _xmins(conn)
    second = _write(conn)
    assert set(second) == set(_KEYS)
    for table, got in second.items():
        assert tuple(got[:3]) == _ZERO, table
        assert got[3] == first[table][0], table
    assert _xmins(conn) == before  # xmin unchanged on every row: no write happened, not just equal values


def test_counts_are_inserted_updated_deleted_unchanged(conn):
    _write(conn)
    got = _write(conn, transactions=(tx("t1", amount="10.00"), tx("t2", amount="21.00")))
    assert got["transactions"] == (0, 1, 0, 1)


def test_one_changed_amount_is_the_only_write(conn):
    _write(conn)
    got = _write(conn, transactions=(tx("t1", amount="10.00"), tx("t2", amount="21.00")))
    assert _iud(got, "transactions") == (0, 1, 0)
    assert all(_iud(got, t) == _ZERO for t in _KEYS if t != "transactions")


def test_new_category_is_one_insert(conn):
    _write(conn)
    got = _write(conn, categories=("food", "misc", "travel"))
    assert _iud(got, "categories") == (1, 0, 0)
    assert all(_iud(got, t) == _ZERO for t in _KEYS if t != "categories")


def test_removed_import_rule_is_one_delete(conn):
    _write(conn)
    got = _write(conn, rules=())
    assert _iud(got, "rules") == (0, 0, 1)
    assert all(_iud(got, t) == _ZERO for t in _KEYS if t != "rules")


def test_changed_rule_ruling_person_and_fx_rate_are_updates_not_replacements(conn):
    _write(conn)
    before = _xmins(conn)
    got = _write(
        conn,
        rules=(_import_rule("r1", description_contains="other"),),
        rulings=(_import_ruling("g1", "t1", "misc"),),
        people=(person("alice", "Alice", "13.00", "n"),),
        fx_rates=(FxRate("x1", date(2026, 1, 5), Decimal("41.3000"), "sell", "bank"),),
    )
    for table in ("rules", "rulings", "people", "fx_rates"):
        assert _iud(got, table) == (0, 1, 0), table
    after = _xmins(conn)
    assert {k for k in before if before[k] != after[k]} == {
        ("rules", "r1"), ("rulings", "g1"), ("people", "alice"), ("fx_rates", "x1")}


def test_changed_owner_budget_flag_moves_without_touching_other_rows(conn):
    _write(conn, owners=(owner("a", budget=True), owner("b", budget=False)),
           accounts=(replace(account(), owner="a"),))
    got = _write(conn, owners=(owner("a", budget=False), owner("b", budget=True)),
                 accounts=(replace(account(), owner="a"),))
    assert _iud(got, "owners") == (0, 2, 0)
    assert got["owners"][3] == 0


def test_import_removed_person_and_fx_rate_are_deleted(conn):
    _write(conn)
    got = _write(conn, people=(), fx_rates=())
    assert _iud(got, "people") == (0, 0, 1)
    assert _iud(got, "fx_rates") == (0, 0, 1)


def test_operator_rule_ruling_and_category_survive_an_import_that_drops_import_ones(conn):
    _write(conn)
    conn.execute(
        "insert into rules (id, kind, priority, ordinal, match, result, active, created_by)"
        " select 'oprule', kind, priority, ordinal, match, result, active, 'operator' from rules where id = 'r1'"
    )
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('op1', 't1', 'category', '{\"category\": \"misc\", \"subcategory\": null}', 'operator')"
    )
    conn.execute("insert into categories (name, created_by) values ('opcat', 'operator')")
    before = _xmins(conn)
    got = _write(conn, rules=(), rulings=(), categories=())
    assert _iud(got, "rules") == (0, 0, 1)
    assert _iud(got, "rulings") == (0, 0, 1)
    assert _iud(got, "categories") == (0, 0, 2)
    after = _xmins(conn)
    for key in (("rules", "oprule"), ("rulings", "op1"), ("categories", "opcat")):
        assert after[key] == before[key]
    assert {k[1] for k in after if k[0] in ("rules", "rulings", "categories")} == {"oprule", "op1", "opcat"}


def test_agent_rows_are_never_compared_or_deleted(conn):
    _write(conn)
    conn.execute("insert into categories (name, created_by) values ('agentcat', 'agent')")
    got = _write(conn)
    assert _iud(got, "categories") == _ZERO
    assert conn.execute("select count(*) as n from categories where name = 'agentcat'").fetchone()["n"] == 1  # type: ignore[index]


def test_stale_transaction_and_its_ruling_are_kept(conn):
    _write(conn)
    got = _write(conn, transactions=(tx("t2", amount="20.00"),), rulings=())
    assert _iud(got, "transactions") == _ZERO
    assert _iud(got, "rulings") == _ZERO  # g1 belongs to stale t1: kept, as before
    assert db.stale_transaction_ids(conn, ["t2"]) == ["t1"]


def test_second_plan_lines_import_is_zero_zero_zero_and_one_dropped_line_is_one_delete(conn):
    assert _iud(_write(conn), "plan_lines") == (2, 0, 0)
    assert _iud(_write(conn), "plan_lines") == _ZERO
    counts = _write(conn, plan_lines=(_plan("food"),))
    assert _iud(counts, "plan_lines") == (0, 0, 1)
    assert [r["id"] for r in conn.execute("select id from plan_lines").fetchall()] == ["2026-01|food||UAH"]


def test_changed_plan_amount_is_one_update(conn):
    _write(conn)
    counts = _write(conn, plan_lines=(_plan("food", amount="150.00"), _plan("misc", "x")))
    assert _iud(counts, "plan_lines") == (0, 1, 0)


def test_plan_lines_none_leaves_the_table_alone_and_returns_no_entry(conn):
    _write(conn)
    counts = _write(conn, plan_lines=None)
    assert "plan_lines" not in counts
    assert conn.execute("select count(*) as n from plan_lines").fetchone()["n"] == 2
