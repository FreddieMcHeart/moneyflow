"""python -m tools.import_old --old-repo PATH --db URL"""

import argparse
from pathlib import Path

import psycopg

from moneyflow.core.model import Owner
from moneyflow.storage import db
from tools.import_old.read import read_accounts, read_categories, read_overrides, read_raw, read_rules


def run(old_repo: Path, conn: psycopg.Connection) -> dict[str, int]:
    data = Path(old_repo) / "data"
    accounts = read_accounts(data / "accounts.yaml")
    transactions = read_raw(data / "monobank_import", accounts)
    known = {t.id for t in transactions}
    overrides = read_overrides(data / "manual.yaml")
    rulings = tuple(g for g in overrides if g.transaction_id in known)
    dropped = [g for g in overrides if g.transaction_id not in known]
    print(f"not imported: {len(dropped)} overrides for transactions outside phase 0 input")
    for g in dropped:
        print(f"  {g.transaction_id}")
    stale = db.stale_transaction_ids(conn, known)
    print(f"not in source: {len(stale)} stored transactions")
    for tid in stale:
        print(f"  {tid}")
    # BRIDGE T2-B1: owners built from the accounts' distinct owners, exactly one budget=True;
    # Task 9 removes it (it reads `owners:` and `budget_owner:` from the old accounts.yaml).
    owner_ids = sorted({a.owner for a in accounts})
    owners = tuple(Owner(id=o, display=o, counterparty_strings=(), budget=(i == 0)) for i, o in enumerate(owner_ids))
    counts = db.replace_imported(
        conn,
        owners=owners,
        accounts=accounts,
        transactions=transactions,
        rules=read_rules(data / "rules.yaml"),
        rulings=rulings,
        categories=read_categories(data / "categories.yaml"),
    )
    for table, n in counts.items():
        print(f"{table}: {n}")
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tools.import_old")
    parser.add_argument("--old-repo", required=True, type=Path)
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    with db.connect(args.db) as conn:
        run(args.old_repo, conn)


if __name__ == "__main__":
    main()
