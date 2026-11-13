"""With MONEYFLOW_TEST_DATABASE_URL unset, a run that collects DB tests is refused once, not errored per test."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESSAGE = "MONEYFLOW_TEST_DATABASE_URL is not set:"


def test_unset_variable_refuses_the_run_once():
    env = {k: v for k, v in os.environ.items() if k != "MONEYFLOW_TEST_DATABASE_URL"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/services/test_rebuild.py", "tests/tools/test_local.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout.count(MESSAGE) == 1
    assert "ERROR at setup" not in result.stdout
