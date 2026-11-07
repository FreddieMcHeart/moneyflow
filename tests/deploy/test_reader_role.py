"""deploy/postgres/reader.sql gives a role SELECT on the app tables and nothing else.

The role name is a psql variable in the real file (`:"reader"`); psycopg has no psql variables, so the test
substitutes them textually and applies the statements on a per-process `_test` database.
"""

import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

READER_SQL = Path(__file__).resolve().parents[2] / "deploy" / "postgres" / "reader.sql"
PASSWORD = "throwaway-" + uuid.uuid4().hex  # random per run, role is dropped in `finally`


def _apply(admin, reader: str, db: str) -> None:
    text = READER_SQL.read_text()
    text = re.sub(r'^\s*--.*$', "", text, flags=re.M)
    text = text.replace(':"reader"', sql.Identifier(reader).as_string()).replace(':"db"', sql.Identifier(db).as_string())
    assert ":" not in re.sub(r"::\w+", "", text).replace("'", ""), "unsubstituted psql variable"
    for statement in text.split(";"):
        if statement.strip():
            admin.execute(statement)


def _reader_url(db_url: str, reader: str) -> str:
    parts = urlsplit(db_url)
    return parts._replace(netloc=f"{quote(reader)}:{quote(PASSWORD)}@{parts.hostname}:{parts.port}").geturl()


@pytest.fixture
def reader_conn(db_url):
    from moneyflow.storage import db

    reader = f"mf_reader_t{os.getpid()}_{uuid.uuid4().hex[:8]}"
    dbname = str(conninfo_to_dict(db_url)["dbname"])
    with db.connect(db_url) as admin:
        admin.execute(sql.SQL("create role {} login password {}").format(sql.Identifier(reader), sql.Literal(PASSWORD)))
        try:
            _apply(admin, reader, dbname)
            with psycopg.connect(_reader_url(db_url, reader), autocommit=True) as conn:
                yield conn
        finally:
            admin.execute(sql.SQL("drop owned by {}").format(sql.Identifier(reader)))
            admin.execute(sql.SQL("drop role {}").format(sql.Identifier(reader)))


def test_reader_can_select_app_tables(reader_conn):
    assert reader_conn.execute("select count(*) from accounts").fetchone()[0] >= 0


@pytest.mark.parametrize(
    "statement",
    [
        "insert into accounts default values",
        "update accounts set id = id",
        "delete from accounts",
        "create table reader_made_this (x int)",
        "truncate accounts",
    ],
)
def test_reader_cannot_write(reader_conn, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader_conn.execute(statement)
