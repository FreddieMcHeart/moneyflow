"""python -m tools.import_old --old-repo PATH --db URL"""

import argparse
from dataclasses import replace
from pathlib import Path

import psycopg

from moneyflow.core.model import Ruling
from moneyflow.storage import db
from tools.import_old.read import (
    read_accounts,
    read_attribute_rulings,
    read_cash,
    read_categories,
    read_debt_rulings,
    read_exclude_rules,
    read_exclude_rulings,
    read_fx_floor_steps,
    read_fx_rates,
    read_manual_transactions,
    read_overrides,
    read_owners,
    read_people,
    read_raw,
    read_reclassify_rules,
    read_reimbursement_rulings,
    read_rules,
    read_split_rulings,
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
    cash_account, cash_from, cash_opening = read_cash(source / "cash.py")
    read = read_accounts(data / "accounts.yaml")
    if cash_account not in {a.id for a in read}:
        raise ValueError(f"cash account {cash_account!r} is not among the imported accounts")
    steps = read_fx_floor_steps(source / "fx.py", read)
    accounts = tuple(
        replace(a, fx_income_floor_step=steps[a.id], **({"cash_from": cash_from, "cash_opening": cash_opening} if a.id == cash_account else {}))
        for a in read
    )
    manual = data / "manual.yaml"
    manual_transactions, manual_rulings = read_manual_transactions(manual, accounts)
    transactions = read_raw(data / "monobank_import", accounts) + manual_transactions
    known = {t.id for t in transactions}
    overrides = read_overrides(manual)
    groups = (
        ("overrides", overrides),
        ("exclude rulings", read_exclude_rulings(manual, source / "exclusions.py")),
        ("white_card rulings", read_white_card_rulings(manual)),
        ("split rulings", read_split_rulings(manual)),
        ("reimbursement rulings", read_reimbursement_rulings(manual)),
        ("debt rulings", read_debt_rulings(manual)),
        ("manual rulings", manual_rulings),
    )
    rulings: tuple[Ruling, ...] = ()
    for label, group in groups:
        rulings += _report_absent(label, group, known)
    attribution = read_attribute_rulings(manual)
    absent = [g.transaction_id for g in attribution if g.transaction_id not in known]
    if absent:
        raise ValueError(f"attribution entries for {len(absent)} transactions not in the raw files: {', '.join(absent)}")
    rulings += attribution
    stale = db.stale_transaction_ids(conn, known)
    print(f"not in source: {len(stale)} stored transactions")
    for tid in stale:
        print(f"  {tid}")
    exclude_rules = read_exclude_rules(source / "exclusions.py")
    reclassify_rules = read_reclassify_rules(source)
    counts = db.replace_imported(
        conn,
        owners=read_owners(data / "accounts.yaml"),
        accounts=accounts,
        transactions=transactions,
        rules=read_rules(data / "rules.yaml") + exclude_rules + reclassify_rules,
        rulings=rulings,
        categories=read_categories(data / "categories.yaml"),
        people=read_people(manual),
        fx_rates=read_fx_rates(data / "history" / "usd_rate.csv"),
    )
    for table, c in counts.items():
        print(f"{table}: {c.inserted} inserted, {c.updated} updated, {c.deleted} deleted, {c.unchanged} unchanged")
    print(f"imported exclude rules: {len(exclude_rules)}")
    print(f"imported reclassify rules: {len(reclassify_rules)}")
    for kind, group in groups[1:]:
        print(f"imported {kind}: {sum(1 for g in group if g.transaction_id in known)}")
    print(f"imported attribute rulings: {len(attribution)}")
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
