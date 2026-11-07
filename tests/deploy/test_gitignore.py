"""The local environment keeps its data and secrets out of git, but `.env.example` stays tracked."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0


def test_local_data_dir_is_ignored():
    assert _ignored(".local/pg/x")


def test_env_is_ignored():
    assert _ignored(".env")


def test_env_example_is_not_ignored():
    assert not _ignored(".env.example")
