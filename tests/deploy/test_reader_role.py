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
def reader_env(db_url):
    """(admin connection, reader role name, reader URL) on a throwaway database that reader.sql is applied to.

    moneyflow_test-style databases never get reader.sql: the database is created with the schema, then dropped
    (with its role) in `finally`.
    """
    from moneyflow.storage import db

    suffix = f"t{os.getpid()}_{uuid.uuid4().hex[:8]}"
    dbname, reader = f"mf_readerenv_{suffix}", f"mf_reader_{suffix}"
    url = _with_db(db_url, dbname)
    with db.connect(db_url) as server:
        server.execute(sql.SQL("create role {} login password {}").format(sql.Identifier(reader), sql.Literal(PASSWORD)))
        try:
            server.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))
            db.migrate(url)
            with db.connect(url) as admin:
                _apply(admin, reader, dbname)
                yield admin, reader, _reader_url(url, reader)
        finally:
            server.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(dbname)))
            server.execute(sql.SQL("drop role {}").format(sql.Identifier(reader)))


@pytest.fixture
def reader_conn(reader_env):
    with psycopg.connect(reader_env[2], autocommit=True) as conn:
        yield conn


def test_reader_can_select_app_tables(reader_conn):
    # The query must run; the row count is irrelevant (an empty table is fine).
    reader_conn.execute("select count(*) from accounts").fetchone()


def test_reader_can_select_a_table_created_later(reader_env, reader_conn):
    """ALTER DEFAULT PRIVILEGES: a table the admin creates after reader.sql ran is readable too."""
    admin = reader_env[0]
    table = f"later_t{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin.execute(sql.SQL("create table {} (x int)").format(sql.Identifier(table)))
    try:
        reader_conn.execute(sql.SQL("select * from {}").format(sql.Identifier(table))).fetchall()
    finally:
        admin.execute(sql.SQL("drop table {}").format(sql.Identifier(table)))


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


@pytest.fixture
def throwaway_db(db_url):
    """An empty database that reader.sql is applied to, plus a second role that was granted nothing."""
    from moneyflow.storage import db

    suffix = f"t{os.getpid()}_{uuid.uuid4().hex[:8]}"
    name, reader, other = f"mf_throwaway_{suffix}", f"mf_reader_{suffix}", f"mf_other_{suffix}"
    with db.connect(db_url) as admin:
        for role in (reader, other):
            admin.execute(sql.SQL("create role {} login password {}").format(sql.Identifier(role), sql.Literal(PASSWORD)))
        try:
            admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
            with db.connect(_with_db(db_url, name)) as inside:
                _apply(inside, reader, name)
            yield db_url, name, reader, other
        finally:
            # The database goes first: its ACL entries are what keep the roles from being dropped.
            admin.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))
            for role in (reader, other):
                admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))


def _with_db(db_url: str, name: str) -> str:
    return urlsplit(db_url)._replace(path="/" + name).geturl()


def _as(db_url: str, name: str, role: str) -> str:
    return _reader_url(_with_db(db_url, name), role)


def test_role_without_a_grant_cannot_connect(throwaway_db):
    """reader.sql revokes CONNECT from PUBLIC: only roles granted it explicitly get in."""
    db_url, name, reader, other = throwaway_db
    with psycopg.connect(_as(db_url, name, reader)) as ok:
        ok.execute("select 1")
    with pytest.raises(psycopg.OperationalError, match="permission denied for database"):
        psycopg.connect(_as(db_url, name, other))


def test_reader_cannot_create_temp_tables(throwaway_db):
    db_url, name, reader, _ = throwaway_db
    with psycopg.connect(_as(db_url, name, reader), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("create temp table reader_temp (x int)")


def test_reader_sql_is_not_applied_to_the_shared_test_database(reader_env, db_url):
    """The fixture works on a throwaway database; the per-process test database never sees the reader role."""
    admin, reader, _ = reader_env
    current = admin.execute("select current_database() as d").fetchone()["d"]
    assert current != urlsplit(db_url).path.lstrip("/")
    from moneyflow.storage import db

    with db.connect(db_url) as other:
        assert other.execute("select has_table_privilege(%s, 'accounts', 'SELECT') as ok", (reader,)).fetchone()["ok"] is False
