"""DB fixtures: a real Postgres at MONEYFLOW_TEST_DATABASE_URL. Unset means FAIL, never skip."""

import os

import pytest

from moneyflow.storage import db

_TABLES = "ledger_entries, rulings, rules, categories, transactions, accounts"


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("MONEYFLOW_TEST_DATABASE_URL")
    if not url:
        pytest.fail("MONEYFLOW_TEST_DATABASE_URL is not set: DB tests need a real Postgres, they never skip")
    with db.connect(url) as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
    db.migrate(url)
    return url


@pytest.fixture
def conn(db_url):
    with db.connect(db_url) as connection:
        connection.execute(f"truncate {_TABLES}")
        yield connection
