"""Command line: `python -m moneyflow rebuild --db URL`, `python -m moneyflow mcp --db URL`."""

import argparse
import os
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date

from moneyflow.core.model import BuildRefused
from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moneyflow")
    sub = parser.add_subparsers(dest="command", required=True)
    default = os.environ.get("MONEYFLOW_DATABASE_URL")
    sub.add_parser("rebuild", help="rebuild the stored ledger").add_argument("--db", default=default)
    sub.add_parser("mcp", help="serve the read-only MCP tools over stdio").add_argument("--db", default=default)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.db:
        parser.error("--db or MONEYFLOW_DATABASE_URL is required")
    if args.command == "mcp":
        from moneyflow.api.mcp_server import create_server

        create_server(args.db).run(transport="stdio")
        return 0
    try:
        with db.connect(args.db) as conn:
            ledger = rebuild(conn, today=date.today())
    except BuildRefused as refusal:
        print(f"refused: {refusal.reason}, transaction {refusal.transaction_id}", file=sys.stderr)
        return 1
    counts = Counter(e.status for e in ledger)
    print(f"kept {counts['kept']}, unresolved {counts['unresolved']}, excluded {counts['excluded']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
