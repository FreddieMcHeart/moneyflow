"""python -m tools.metabase: provision Metabase (admin, data connection, collection, dashboards)."""

from collections.abc import Sequence
from pathlib import Path

from tools.local import read_env
from tools.metabase.api import ApiError, Client, load_definitions, provision

ROOT = Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None, *, root: Path = ROOT) -> int:
    del argv
    env = read_env(root / ".env")
    client = Client(f"http://localhost:{env.get('MF_MB_PORT', '3000')}")
    try:
        counts = provision(client, env, load_definitions(root / "deploy" / "metabase" / "dashboards"))
    except (ApiError, ValueError, KeyError) as error:
        print(f"metabase: failed ({type(error).__name__}: {str(error)[:120]})")
        return 1
    print(f"metabase: {counts.line()}")
    return 0
