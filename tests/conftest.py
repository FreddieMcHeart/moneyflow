"""DB fixtures: a real Postgres at MONEYFLOW_TEST_DATABASE_URL. Unset means FAIL, never skip."""

import os
from urllib.parse import urlparse

import pytest

from moneyflow.storage import db

_TABLES = "ledger_entries, rulings, rules, categories, transactions, accounts"


def require_test_db(url: str) -> None:
    """The fixture drops schema public: refuse any database whose name has no "test"."""
    name = urlparse(url).path.lstrip("/")
    if "test" not in name:
        pytest.fail(f"refusing to drop schema public in database {name!r}: its name has no 'test'")


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("MONEYFLOW_TEST_DATABASE_URL")
    if not url:
        pytest.fail("MONEYFLOW_TEST_DATABASE_URL is not set: DB tests need a real Postgres, they never skip")
    require_test_db(url)
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
