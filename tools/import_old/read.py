"""Readers for the old repo's data files. Every reader is read-only and builds core model objects."""

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from moneyflow.core.model import Account, Rule, Ruling, Transaction

_DIRECTIONS = ("income", "expense")
_AMOUNT_KEYS = ("amount_lt", "amount_gte")


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_accounts(path: Path) -> tuple[Account, ...]:
    return tuple(
        Account(
            id=str(a["id"]), name=str(a["name"]), currency=str(a["currency"]),
            owner=str(a["owner"]), role=str(a["role"]), include=bool(a["include"]),
            cash_from=None,
        )
        for a in _load(path)["accounts"]
    )


def read_categories(path: Path) -> frozenset[str]:
    return frozenset(str(c["name"]) for c in _load(path)["categories"])


def _match(raw: dict) -> dict[str, object]:
    """Amount thresholds are stored as strings so no float ever reaches the database."""
    return {k: (str(v) if k in _AMOUNT_KEYS else v) for k, v in raw.items()}


def read_rules(path: Path) -> tuple[Rule, ...]:
    return tuple(
        Rule(
            id=str(r["name"]), kind="categorize", priority=int(r["priority"]), ordinal=i,
            match=_match(r["match"]),
            result={"category": r["category"], "subcategory": r.get("subcategory")},
            active=True, created_by="import",
        )
        for i, r in enumerate(_load(path)["rules"])
    )


def read_overrides(path: Path) -> tuple[Ruling, ...]:
    return tuple(
        Ruling(
            id=f"override:{tx_id}", transaction_id=str(tx_id), kind="category",
            payload={"category": o["category"], "subcategory": o.get("subcategory"), "note": o.get("note")},
            created_by="import",
        )
        for tx_id, o in _load(path)["overrides"].items()
    )


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _transaction(row: dict, account: Account) -> Transaction:
    tid = row["id"]
    amount = Decimal(row["amount_uah"] if "amount_uah" in row else row["amount"])
    if amount < 0:
        raise ValueError(f"negative amount (transaction {tid})")
    if row["direction"] not in _DIRECTIONS:
        raise ValueError(f"direction {row['direction']!r} is not income or expense (transaction {tid})")
    declared = (row.get("currency") or "").strip()
    if declared and declared != account.currency:
        raise ValueError(f"currency {declared!r} differs from account {account.id} ({account.currency}) (transaction {tid})")
    return Transaction(
        id=tid, account_id=account.id, date=date.fromisoformat(row["date"]), time=_utc(row["time"]),
        amount=amount, currency=account.currency, direction=row["direction"],
        mcc=int(row["mcc"]) if row["mcc"] else None,
        description=row["description"], comment=row["comment"],
    )


def _file_rows(path: Path, accounts: dict[str, Account]) -> list[Transaction]:
    account_id = path.name.rsplit("_", 2)[0]
    if account_id not in accounts:
        raise ValueError(f"raw file {path.name} belongs to unknown account {account_id!r}")
    with open(path, encoding="utf-8", newline="") as handle:
        return [_transaction(row, accounts[account_id]) for row in csv.DictReader(handle)]


def read_raw(directory: Path, accounts: tuple[Account, ...]) -> tuple[Transaction, ...]:
    """`directory/*.csv`, no recursion. Last copy of an id wins; ids whose copies differ are printed."""
    by_id = {a.id: a for a in accounts}
    kept: dict[str, Transaction] = {}
    differing: list[str] = []
    for path in sorted(Path(directory).glob("*.csv")):
        for t in _file_rows(path, by_id):
            if t.id in kept and kept[t.id] != t and t.id not in differing:
                differing.append(t.id)
            kept[t.id] = t
    print(f"differing duplicates: {len(differing)}")
    for tid in differing:
        print(f"  {tid}")
    return tuple(kept.values())
