"""Migration 0005: month_summaries exists at head, and the downgrade drops it."""

from alembic import command
from alembic.config import Config

from moneyflow.storage import db

_PREVIOUS = "0004_cycle_fx_inputs"
_COLUMNS = [
    "budget_month", "currency", "income", "total_actual", "cycle_start", "cycle_end", "cycle_open",
    "days_total", "days_left", "cash_in_hand", "built_for",
]


def _exists(conn):
    row = conn.execute("select to_regclass('month_summaries') as t").fetchone()
    return row["t"] is not None


def test_head_has_month_summaries_with_its_columns(conn):
    assert _exists(conn)
    rows = conn.execute(
        "select column_name from information_schema.columns where table_name = 'month_summaries'"
        " order by ordinal_position"
    ).fetchall()
    assert [r["column_name"] for r in rows] == _COLUMNS


def test_primary_key_is_month_and_currency(conn):
    rows = conn.execute(
        "select a.attname from pg_index i join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)"
        " where i.indrelid = 'month_summaries'::regclass and i.indisprimary order by a.attname"
    ).fetchall()
    assert [r["attname"] for r in rows] == ["budget_month", "currency"]


def test_downgrade_drops_the_table_and_upgrade_restores_it(db_url, conn):
    config = Config(str(db._ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", db_url)
    try:
        command.downgrade(config, _PREVIOUS)
        assert not _exists(conn)
    finally:
        db.migrate(db_url)
    assert _exists(conn)
