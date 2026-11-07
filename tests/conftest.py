"""DB fixtures: a real Postgres at MONEYFLOW_TEST_DATABASE_URL. Unset means FAIL, never skip."""

import os
from urllib.parse import urlsplit

import pytest
from psycopg.conninfo import conninfo_to_dict

from moneyflow.storage import db

_TABLES = "fx_rates, month_summaries, ledger_entries, rulings, rules, categories, people, transactions, accounts, owners"


def require_test_db(url: str) -> None:
    """The fixture creates and drops databases from this one: refuse any database whose name does not end in "_test"."""
    name = str(conninfo_to_dict(url).get("dbname", ""))
    if not name.endswith("_test"):
        pytest.fail(f"refusing to run tests from database {name!r}: its name does not end in '_test'")


@pytest.fixture(scope="session")
def db_url():
    """One database per pytest process, created from the configured one and dropped after.

    The mutation gate runs several full suites at once; on one shared database they truncate each other's rows and
    a broken run is counted as a killed mutant.
    """
    url = os.environ.get("MONEYFLOW_TEST_DATABASE_URL")
    if not url:
        pytest.fail("MONEYFLOW_TEST_DATABASE_URL is not set: DB tests need a real Postgres, they never skip")
    require_test_db(url)
    base = str(conninfo_to_dict(url)["dbname"])
    name = f"{base.removesuffix('_test')}_{os.getpid()}_test"
    with db.connect(url) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')
        admin.execute(f'create database "{name}"')
    own = urlsplit(url)._replace(path=f"/{name}").geturl()  # a URL, not a DSN: SQLAlchemy reads it too
    db.migrate(own)
    yield own
    with db.connect(url) as admin:
        admin.execute(f'drop database "{name}" with (force)')


@pytest.fixture
def conn(db_url):
    with db.connect(db_url) as connection:
        connection.execute(f"truncate {_TABLES}")
        yield connection
