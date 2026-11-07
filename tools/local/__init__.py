"""Local environment steps. Every step takes a runner (`runner(cmd, env=None) -> exit code`), so tests
replace subprocess, the database and the clock. Passwords are never printed."""

import re
import secrets
import shutil
import sys
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = "deploy/compose.yml"
PLACEHOLDER_OLD_REPO = "/path/to/old/repo"
GENERATED = ("POSTGRES_PASSWORD", "MF_READER_PASSWORD", "MB_DB_PASSWORD", "MB_ADMIN_PASSWORD")

Runner = Callable[..., int]
Which = Callable[[str], str | None]


class Refused(Exception):
    """A precondition failed; the message is the one clear line shown to the operator."""


def detect_compose(runner: Runner, which: Which = shutil.which, override: str | None = None) -> list[str]:
    """`MF_COMPOSE` (for example "podman compose") wins; otherwise `docker compose`, then `podman compose`."""
    if override:
        return override.split()
    for binary in ("docker", "podman"):
        if which(binary) and runner([binary, "compose", "version"]) == 0:
            return [binary, "compose"]
    raise Refused("neither `docker compose` nor `podman compose` is available; install one or set MF_COMPOSE")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def ensure_env(root: Path) -> bool:
    """Create .env from .env.example with random passwords. Never overwrites. True if created."""
    target = root / ".env"
    if target.exists():
        return False
    text = (root / ".env.example").read_text()
    for key in GENERATED:
        text = re.sub(rf"^{key}=.*$", lambda _m, k=key: f"{k}={secrets.token_urlsafe(24)}", text, flags=re.M)
    with open(target, "x") as handle:
        handle.write(text)
    target.chmod(0o600)
    return True


def local_url(env: dict[str, str], *, mask: bool = False) -> str:
    password = "***" if mask else quote(env["POSTGRES_PASSWORD"], safe="")
    return f"postgresql://postgres:{password}@localhost:{env.get('MF_PG_PORT', '5434')}/moneyflow_dev"


def _compose_cmd(compose: list[str], root: Path, *args: str) -> list[str]:
    return [*compose, "-f", str(root / COMPOSE_FILE), "--env-file", str(root / ".env"), *args]


def _py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def import_and_rebuild(env: dict[str, str], runner: Runner) -> int:
    url = local_url(env)
    code = runner(_py("tools.import_old", "--old-repo", env["MF_OLD_REPO"], "--db", url))
    return code or runner(_py("moneyflow", "rebuild", "--db", url))


def check_old_repo(env: dict[str, str]) -> str:
    old = env.get("MF_OLD_REPO", "")
    if not old or old == PLACEHOLDER_OLD_REPO:
        raise Refused("MF_OLD_REPO is not set in .env; point it at the old repo (read only) and run again")
    if not Path(old).is_dir():
        raise Refused("MF_OLD_REPO in .env is not a directory")
    return old


def up(root: Path, runner: Runner, *, which: Which = shutil.which, environ: dict[str, str] | None = None, out=print) -> int:
    environ = environ or {}
    try:
        compose = detect_compose(runner, which, environ.get("MF_COMPOSE"))
        if ensure_env(root):
            out("created .env with random passwords (not shown)")
        env = read_env(root / ".env")
        check_old_repo(env)
    except Refused as refusal:
        out(f"refused: {refusal}")
        return 2
    steps: list[tuple[str, Callable[[], int]]] = [
        ("containers", lambda: runner(_compose_cmd(compose, root, "up", "-d", "--wait"))),
        ("migrations", lambda: runner(_py("alembic", "upgrade", "head"), env={"MONEYFLOW_DATABASE_URL": local_url(env)})),
        ("import and rebuild", lambda: import_and_rebuild(env, runner)),
        ("metabase", lambda: runner(_py("tools.metabase"))),
    ]
    for name, step in steps:
        out(f"== {name}")
        code = step()
        if code:
            out(f"failed at step '{name}', exit {code}")
            return code
    out(f"postgres: {local_url(env, mask=True)}")
    out(f"metabase: http://localhost:{env.get('MF_MB_PORT', '3000')}")
    return 0


def refresh(root: Path, runner: Runner, *, out=print) -> int:
    try:
        env = read_env(root / ".env")
        check_old_repo(env)
    except (Refused, FileNotFoundError) as refusal:
        out(f"refused: {refusal}")
        return 2
    return import_and_rebuild(env, runner)


def stop(root: Path, runner: Runner, *, which: Which = shutil.which, environ: dict[str, str] | None = None, out=print) -> int:
    try:
        compose = detect_compose(runner, which, (environ or {}).get("MF_COMPOSE"))
    except Refused as refusal:
        out(f"refused: {refusal}")
        return 2
    return runner(_compose_cmd(compose, root, "stop"))


def _health(port: str) -> str:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/health", timeout=3) as response:
            return response.read(200).decode()
    except OSError as error:
        return f"unreachable ({type(error).__name__})"


def _query(url: str) -> dict:
    import psycopg

    with psycopg.connect(url) as conn:
        tables = [r[0] for r in conn.execute("select tablename from pg_tables where schemaname='public' order by 1")]
        counts = {t: conn.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
        revision = conn.execute("select version_num from alembic_version").fetchone() if "alembic_version" in tables else None
        built = conn.execute("select max(built_for) from month_summaries").fetchone() if "month_summaries" in tables else None
    return {"counts": counts, "revision": revision[0] if revision else None, "built_for": built[0] if built else None}


def status(root: Path, runner: Runner, *, which: Which = shutil.which, environ: dict[str, str] | None = None,
           query: Callable[[str], dict] = _query, health: Callable[[str], str] = _health, out=print) -> int:
    try:
        compose = detect_compose(runner, which, (environ or {}).get("MF_COMPOSE"))
        env = read_env(root / ".env")
    except (Refused, FileNotFoundError) as refusal:
        out(f"refused: {refusal}")
        return 2
    runner(_compose_cmd(compose, root, "ps"))
    try:
        info = query(local_url(env))
        out(f"alembic revision: {info['revision']}")
        out(f"built_for: {info['built_for']}")
        for table, count in info["counts"].items():
            out(f"  {table}: {count}")
    except Exception as error:  # status reports, it does not crash on a down database
        out(f"database: unreachable ({type(error).__name__})")
    out(f"metabase /api/health: {health(env.get('MF_MB_PORT', '3000'))}")
    return 0


def main(argv: Sequence[str] | None = None, *, root: Path = ROOT, runner: Runner | None = None) -> int:
    import argparse
    import os
    import subprocess

    parser = argparse.ArgumentParser(prog="local.sh", description="Local Postgres + Metabase environment")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("up", help="start containers, migrate, import, rebuild, provision Metabase")
    sub.add_parser("refresh", help="import and rebuild only")
    sub.add_parser("stop", help="stop the containers")
    sub.add_parser("status", help="containers, revision, row counts, built_for, Metabase health")
    args = parser.parse_args(argv)

    def run(cmd: list[str], env: dict[str, str] | None = None) -> int:
        probe = cmd[-2:] == ["compose", "version"]
        result = subprocess.run(cmd, cwd=root, env={**os.environ, **(env or {})},
                                stdout=subprocess.DEVNULL if probe else None, stderr=subprocess.DEVNULL if probe else None)
        return result.returncode

    runner = runner or run
    environ = dict(os.environ)
    if args.command == "up":
        return up(root, runner, environ=environ)
    if args.command == "refresh":
        return refresh(root, runner)
    if args.command == "stop":
        return stop(root, runner, environ=environ)
    return status(root, runner, environ=environ)
