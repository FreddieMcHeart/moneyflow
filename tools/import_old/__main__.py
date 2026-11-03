"""python -m tools.import_old --old-repo PATH --db URL"""

import argparse
from dataclasses import replace
from pathlib import Path

import psycopg

from moneyflow.core.model import Ruling
from moneyflow.storage import db
from tools.import_old.read import (
    read_accounts,
    read_cash,
    read_categories,
    read_exclude_rules,
    read_exclude_rulings,
    read_overrides,
    read_owners,
    read_raw,
    read_rules,
    read_white_card_rulings,
)


def _report_absent(label: str, rulings: tuple[Ruling, ...], known: set[str]) -> tuple[Ruling, ...]:
    kept = tuple(g for g in rulings if g.transaction_id in known)
    dropped = [g for g in rulings if g.transaction_id not in known]
    print(f"not imported: {len(dropped)} {label} for transactions outside phase 0 input")
    for g in dropped:
        print(f"  {g.transaction_id}")
    return kept


def run(old_repo: Path, conn: psycopg.Connection) -> dict[str, int]:
    """Import the old repo into the database.

    A10's old regex read the description only; the new `description_contains` rule reads description and
    comment. 0 live rows differ and no new match key was added (see `read_exclude_rules`).
    """
    data = Path(old_repo) / "data"
    source = Path(old_repo) / "scripts" / "python" / "finance"
    cash_account, cash_from = read_cash(source / "cash.py")
    accounts = tuple(
        replace(a, cash_from=cash_from) if a.id == cash_account else a for a in read_accounts(data / "accounts.yaml")
    )
    transactions = read_raw(data / "monobank_import", accounts)
    known = {t.id for t in transactions}
    manual = data / "manual.yaml"
    overrides = read_overrides(manual)
    white, pending = read_white_card_rulings(manual, overrides)
    exclude = read_exclude_rulings(manual, source / "exclusions.py")
    rulings = _report_absent("overrides", overrides, known)
    rulings += _report_absent("exclude rulings", exclude, known)
    rulings += _report_absent("white_card rulings", white, known)
    rulings += _report_absent("split_pending rulings", pending, known)
    stale = db.stale_transaction_ids(conn, known)
    print(f"not in source: {len(stale)} stored transactions")
    for tid in stale:
        print(f"  {tid}")
    exclude_rules = read_exclude_rules(source / "exclusions.py")
    counts = db.replace_imported(
        conn,
        owners=read_owners(data / "accounts.yaml"),
        accounts=accounts,
        transactions=transactions,
        rules=read_rules(data / "rules.yaml") + exclude_rules,
        rulings=rulings,
        categories=read_categories(data / "categories.yaml"),
        people=(),  # BRIDGE T4-B1: no people read yet; Task 10 removes it
    )
    for table, n in counts.items():
        print(f"{table}: {n}")
    print(f"imported exclude rules: {len(exclude_rules)}")
    for kind, group in (("exclude rulings", exclude), ("white_card rulings", white), ("split_pending rulings", pending)):
        print(f"imported {kind}: {sum(1 for g in group if g.transaction_id in known)}")
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
