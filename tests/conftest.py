"""DB fixtures: a real Postgres at MONEYFLOW_TEST_DATABASE_URL. Unset means FAIL, never skip."""

import os
import re
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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by someone else
    return True


def drop_stale_databases(admin, base: str) -> None:
    """Drop `<base>_<pid>_test` databases left behind by a worker that was killed before its teardown."""
    pattern = re.compile(rf"{re.escape(base)}_(\d+)_test")
    for row in admin.execute("select datname from pg_database").fetchall():
        name = row["datname"]
        match = pattern.fullmatch(name)
        if match and not _pid_alive(int(match.group(1))):
            admin.execute(f'drop database if exists "{name}" with (force)')


def pytest_collection_modifyitems(config, items):
    """Refuse the whole run once when DB tests are collected and the test database is not configured."""
    if os.environ.get("MONEYFLOW_TEST_DATABASE_URL"):
        return
    needing = sum(1 for item in items if {"conn", "db_url"} & set(item.fixturenames))
    if needing:
        pytest.exit(
            f"MONEYFLOW_TEST_DATABASE_URL is not set: {needing} collected tests need a real Postgres "
            "(postgresql://.../moneyflow_test on mf-pg 5433, see AGENTS.md 'Local Postgres'). DB tests never skip.",
            returncode=1,
        )


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
        drop_stale_databases(admin, base.removesuffix("_test"))
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
