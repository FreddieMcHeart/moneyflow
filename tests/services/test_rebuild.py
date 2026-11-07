from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from moneyflow.__main__ import main
from moneyflow.core.model import BuildRefused, LedgerEntry
from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db
from tests.core._builders import TODAY, account, attribute_ruling, owner, rule, ruling, tx


def _seed(conn, rulings=()):
    db.replace_imported(
        conn,
        owners=(owner(),),
        accounts=(account(),),
        transactions=(tx("t1", description="cafe"), tx("t2", description="other")),
        rules=(replace(rule("r1", 10, 0, "food", None, description_contains="cafe"), created_by="import"),),
        rulings=rulings,
        categories=("food",), people=(), fx_rates=())


def test_rebuild_saves_ledger(conn):
    _seed(conn)
    ledger = rebuild(conn, today=TODAY)
    assert db.load_ledger(conn) == ledger
    assert sorted((e.transaction_id, e.status) for e in ledger) == [("t1", "kept"), ("t2", "unresolved")]


def test_refusal_saves_nothing(conn):
    _seed(conn)
    prior = rebuild(conn, today=TODAY)
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('bad', 't1', 'category', '{\"category\": \"nope\"}', 'operator')"
    )
    with pytest.raises(BuildRefused):
        rebuild(conn, today=TODAY)
    assert db.load_ledger(conn) == prior
    assert prior


def test_cli_exit_1_on_refusal(conn, db_url, capsys):
    _seed(conn)
    prior = rebuild(conn, today=TODAY)
    assert prior
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('bad', 't1', 'category', '{\"category\": \"nope\"}', 'operator')"
    )
    assert main(["rebuild", "--db", db_url]) == 1
    err = capsys.readouterr().err
    assert "unknown category nope" in err and "t1" in err
    assert db.load_ledger(conn) == prior


def test_cli_exit_1_on_broken_rule_value(conn, db_url, capsys):
    db.replace_imported(
        conn, owners=(owner(),), accounts=(account(),), transactions=(tx("t1"),),
        rules=(replace(rule("r1", 10, 0, "food", None, amount_lt="abc"), created_by="import"),),
        rulings=(), categories=("food",), people=(), fx_rates=())
    assert main(["rebuild", "--db", db_url]) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused: rule r1") and "Traceback" not in err


def test_cli_prints_counts(conn, db_url, capsys):
    _seed(conn)
    assert main(["rebuild", "--db", db_url]) == 0
    assert capsys.readouterr().out.strip() == "kept 1, unresolved 1, excluded 0"


def test_rebuild_passes_today(conn, monkeypatch):
    _seed(conn)
    seen = []
    real = db.load_inputs

    def spy(c, *, today):
        seen.append(today)
        return real(c, today=today)

    monkeypatch.setattr(db, "load_inputs", spy)
    rebuild(conn, today=date(2030, 5, 17))
    assert seen == [date(2030, 5, 17)]


def test_rebuild_requires_today(conn):
    _seed(conn)
    with pytest.raises(TypeError):
        rebuild(conn)  # type: ignore[call-arg]


def test_cli_passes_the_clock_to_rebuild(conn, db_url, monkeypatch):
    _seed(conn)
    seen = []
    monkeypatch.setattr("moneyflow.__main__.rebuild", lambda c, *, today: seen.append(today) or ())
    assert main(["rebuild", "--db", db_url]) == 0
    assert seen == [date.today()]


NOW = date(2026, 2, 10)


# --- month_summaries: written with the ledger, same transaction, values from core.reports ---

def _seed_cycle(conn):
    """Two attributed salaries and one expense each: cycle 2026-01 closed (has days), 2026-02 open, UAH only."""
    db.replace_imported(
        conn, owners=(owner(),), accounts=(account("inc", role="income"), account()),
        transactions=(tx("pay", account_id="inc", amount="1000.00", direction="income", day=1),
                      tx("t2", description="bus", day=5),
                      tx("pay2", account_id="inc", amount="2000.00", direction="income", day=1, month=2),
                      tx("t3", description="bus", day=5, month=2, amount="30.00")),
        rules=(replace(rule("r1", 10, 0, "Cafe", None, description_contains="bus"), created_by="import"),),
        rulings=(attribute_ruling("pay", "2026-01"), attribute_ruling("pay2", "2026-02")), categories=("Cafe", "Income"), people=(), fx_rates=())


def _core_summaries(conn, today):
    from moneyflow.core.cycles import build_cycles
    from moneyflow.core.reports import month_summaries

    inputs = db.load_inputs(conn, today=today)
    ledger = db.load_ledger(conn)
    return month_summaries(ledger, build_cycles(list(ledger), inputs), inputs)


def _stored(conn):
    return conn.execute("select * from month_summaries order by budget_month, currency").fetchall()


def test_rebuild_writes_one_row_per_month_and_currency_equal_to_core(conn):
    _seed_cycle(conn)
    rebuild(conn, today=NOW)
    expected = []
    for s in _core_summaries(conn, NOW):
        for currency in sorted(set(s.income) | set(s.total_actual)):
            expected.append((s.budget_month, currency, s.income.get(currency, Decimal(0)),
                             s.total_actual.get(currency, Decimal(0)), s.cycle_start, s.cycle_end, s.cycle_open,
                             s.days_total, s.days_left, s.cash_in_hand, NOW))
    assert expected
    got = [(r["budget_month"], r["currency"], r["income"], r["total_actual"], r["cycle_start"], r["cycle_end"],
            r["cycle_open"], r["days_total"], r["days_left"], r["cash_in_hand"], r["built_for"])
           for r in _stored(conn)]
    assert got == expected
    assert [(r["budget_month"], r["currency"]) for r in _stored(conn)] == [("2026-01", "UAH"), ("2026-02", "UAH")]
    first = _stored(conn)[0]
    assert (first["income"], first["total_actual"]) == (Decimal("1000.00"), Decimal("-10.00"))
    assert first["days_total"] is not None and first["days_left"] is not None  # closed cycle: the day columns are set


def test_rebuild_replaces_summaries(conn):
    _seed_cycle(conn)
    rebuild(conn, today=NOW)
    conn.execute("update month_summaries set income = 1")
    rebuild(conn, today=NOW)
    assert _stored(conn)[0]["income"] == Decimal("1000.00")


def test_refusal_leaves_both_tables_as_they_were(conn):
    _seed_cycle(conn)
    rebuild(conn, today=NOW)
    before_summaries, before_ledger = _stored(conn), db.load_ledger(conn)
    assert before_summaries and before_ledger
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('bad', 't2', 'category', '{\"category\": \"nope\"}', 'operator')"
    )
    with pytest.raises(BuildRefused):
        rebuild(conn, today=date(2026, 2, 20))
    assert _stored(conn) == before_summaries
    assert db.load_ledger(conn) == before_ledger


def test_failed_summary_insert_rolls_the_ledger_back(conn):
    """Both tables are written in one transaction: a bad summary row must not leave a replaced ledger behind."""
    _seed_cycle(conn)
    ledger = rebuild(conn, today=NOW)
    stored_ledger, old = db.load_ledger(conn), _stored(conn)
    one = _core_summaries(conn, NOW)[0]
    duplicated = (one, one)  # same (budget_month, currency) twice: the primary key refuses the second
    new_ledger = tuple(e for e in ledger if e.transaction_id != "t2")
    with pytest.raises(Exception):
        db.save_ledger(conn, new_ledger, summaries=duplicated, built_for=NOW)
    assert db.load_ledger(conn) == stored_ledger
    assert _stored(conn) == old


def test_stored_summary_equals_the_on_demand_query_for_the_same_today(conn):
    from moneyflow.services import queries

    _seed_cycle(conn)
    today = date(2026, 2, 20)
    rebuild(conn, today=today)
    rows = _stored(conn)
    assert rows
    for r in rows:
        s = queries.month_summary(conn, r["budget_month"], today=today)
        assert (r["income"], r["total_actual"]) == (s.income[r["currency"]], s.total_actual.get(r["currency"], Decimal(0)))
        assert (r["cycle_start"], r["cycle_end"], r["cycle_open"], r["days_total"], r["days_left"], r["cash_in_hand"]) == (
            s.cycle_start, s.cycle_end, s.cycle_open, s.days_total, s.days_left, s.cash_in_hand)
        assert r["built_for"] == today
