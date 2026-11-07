import os
import subprocess
import sys

import pytest
from psycopg.conninfo import conninfo_to_dict

from moneyflow.storage import db


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _exists(admin, name: str) -> bool:
    return admin.execute("select 1 from pg_database where datname = %s", (name,)).fetchone() is not None


def test_session_start_drops_per_pid_databases_of_dead_workers(db_url):
    url = os.environ["MONEYFLOW_TEST_DATABASE_URL"]
    base = str(conninfo_to_dict(url)["dbname"]).removesuffix("_test")
    stale = f"{base}_{_dead_pid()}_test"
    unrelated = f"{base}_notapid_test"
    with db.connect(url) as admin:
        for name in (stale, unrelated):
            admin.execute(f'drop database if exists "{name}" with (force)')
            admin.execute(f'create database "{name}"')
    try:
        # a real second pytest session: its db_url fixture is the session start under test
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/storage/test_db.py::test_inactive_rules_are_not_loaded"],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stdout[-500:]
        with db.connect(url) as admin:
            assert not _exists(admin, stale), "stale per-pid database survived session start"
            assert _exists(admin, unrelated), "a database that is not <base>_<pid>_test was dropped"
    finally:
        with db.connect(url) as admin:
            for name in (stale, unrelated):
                admin.execute(f'drop database if exists "{name}" with (force)')
