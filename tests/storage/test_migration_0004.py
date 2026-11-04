"""Migration 0004 downgrade: derived outside_cycle ledger rows go, operator attribute rulings block it."""
import pytest
from alembic import command
from alembic.config import Config

from moneyflow.storage import db

_PREVIOUS = "0003_classify_inputs"


def _downgrade(url):
    config = Config(str(db._ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, _PREVIOUS)


def _ledger_row(conn, status):
    conn.execute(
        "insert into ledger_entries (transaction_id, part, date, account_id, amount, currency, status, direction)"
        " values (%s, 0, '2026-10-01', 'a1', -1, 'UAH', %s, 'expense')",
        (f"t-{status}", status),
    )


def _attribute_ruling(conn):
    conn.execute("insert into owners (id, display, counterparty_strings, budget) values ('o1', 'O', '{}', true)")
    conn.execute(
        "insert into accounts (id, name, currency, owner, role, include, fx_income_floor_step)"
        " values ('a1', 'A', 'UAH', 'o1', 'spending', true, 5)"
    )
    conn.execute(
        "insert into transactions (id, account_id, date, time, amount, currency, direction, description, comment, source)"
        " values ('t1', 'a1', '2026-10-01', '2026-10-01T10:00:00Z', 1, 'UAH', 'expense', 'x', '', 'bank')"
    )
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('r1', 't1', 'attribute', '{}', 'operator')"
    )


def test_downgrade_deletes_outside_cycle_ledger_rows(db_url, conn):
    _ledger_row(conn, "outside_cycle")
    _ledger_row(conn, "kept")
    try:
        _downgrade(db_url)
        statuses = [r["status"] for r in conn.execute("select status from ledger_entries").fetchall()]
        assert statuses == ["kept"]
    finally:
        db.migrate(db_url)


def test_downgrade_refuses_while_attribute_rulings_exist(db_url, conn):
    _attribute_ruling(conn)
    _ledger_row(conn, "outside_cycle")
    try:
        with pytest.raises(RuntimeError, match="1 attribute ruling"):
            _downgrade(db_url)
        assert conn.execute("select count(*) as n from rulings where kind = 'attribute'").fetchone()["n"] == 1
        assert conn.execute("select count(*) as n from ledger_entries").fetchone()["n"] == 1
        assert conn.execute("select count(*) as n from fx_rates").fetchone()["n"] == 0
    finally:
        db.migrate(db_url)
