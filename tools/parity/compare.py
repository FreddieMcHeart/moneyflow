"""Parity of the new ledger against the old one, for rows decided by rules and overrides."""

from dataclasses import dataclass
from decimal import Decimal

from moneyflow.core.model import Ledger, LedgerEntry

_OVERRIDE = "override:manual"


@dataclass(frozen=True)
class ParityResult:
    population: int
    compared: int
    differ: list[tuple[str, str]]  # (transaction_id, field)
    missing: list[str]
    out_of_scope: list[str]


def _signed(old: dict[str, str]) -> Decimal | None:
    amount = Decimal(old["amount"])
    return {"income": amount, "expense": -amount}.get(old["direction"])


def _decision_ok(rule: str, decided_by: str | None) -> bool:
    if decided_by is None:
        return False
    if rule == _OVERRIDE:
        return decided_by.startswith("ruling:")
    return decided_by == f"rule:{rule}"


def _field_diffs(old: dict[str, str], new: LedgerEntry) -> list[str]:
    signed = _signed(old)
    if signed is None:
        return ["direction"]
    checks = {
        "category": old["category"] == new.category,
        "subcategory": (old["subcategory"] or None) == new.subcategory,
        "currency": old["currency"] == new.currency,
        "amount": signed == new.amount,
        "decided_by": _decision_ok(old["rule"], new.decided_by),
    }
    return [field for field, ok in checks.items() if not ok]


def compare(old_rows: list[dict[str, str]], rule_names: set[str], raw_ids: set[str], new: Ledger) -> ParityResult:
    first_parts = {e.transaction_id: e for e in new if e.part == 0}
    population = [r for r in old_rows if r["rule"] in rule_names or r["rule"] == _OVERRIDE]
    in_scope = [r for r in population if r["id"] in raw_ids]
    differ = [
        (r["id"], field)
        for r in in_scope
        if r["id"] in first_parts
        for field in _field_diffs(r, first_parts[r["id"]])
    ]
    return ParityResult(
        population=len(population),
        compared=len(in_scope),
        differ=differ,
        missing=sorted(raw_ids - first_parts.keys()),
        out_of_scope=[r["id"] for r in population if r["id"] not in raw_ids],
    )
