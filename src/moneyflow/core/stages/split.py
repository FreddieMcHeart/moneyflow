"""A29: split a kept or unresolved transaction into parts; the remainder keeps the parent's own category, or none."""

from dataclasses import replace

from moneyflow.core.kinds.split import SplitPart, apply_ruling
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry

REASON = "split"


def _parts_by_transaction(inputs: Inputs) -> dict[str, list[SplitPart]]:
    grouped: dict[str, list[SplitPart]] = {}
    for ruling in (r for r in inputs.rulings if r.kind == "split"):
        grouped.setdefault(ruling.transaction_id, []).append(apply_ruling(ruling))
    for transaction_id, parts in grouped.items():
        parts.sort(key=lambda p: p.n)
        if [p.n for p in parts] != list(range(1, len(parts) + 1)):
            raise BuildRefused("split part numbers have a gap or a duplicate", transaction_id)
    return grouped


def _part(parent: LedgerEntry, part: SplitPart, inputs: Inputs) -> LedgerEntry:
    if part.category not in inputs.categories:
        raise BuildRefused(f"unknown category {part.category}", parent.transaction_id)
    decided_by = f"ruling:{part.ruling_id}"
    return replace(
        parent,
        part=part.n,
        status="kept",
        reason=None,
        date=part.date or parent.date,
        amount=part.amount,
        category=part.category,
        subcategory=part.subcategory,
        decided_by=decided_by,
        via=decided_by,
    )


def _split_entry(parent: LedgerEntry, parts: list[SplitPart], inputs: Inputs) -> list[LedgerEntry]:
    if parent.status == "excluded":
        raise BuildRefused(f"split ruling on an {parent.status} entry", parent.transaction_id)
    remainder = parent.amount - sum((p.amount for p in parts), start=parent.amount * 0)
    if remainder < 0:
        raise BuildRefused("split parts exceed the transaction amount", parent.transaction_id)
    out = [replace(parent, status="excluded", reason=REASON, decided_by=f"core:{REASON}")]
    out.extend(_part(parent, p, inputs) for p in parts)
    if remainder > 0:
        out.append(replace(parent, part=len(parts) + 1, amount=remainder))
    return out


def split(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    by_transaction = _parts_by_transaction(inputs)
    out: list[LedgerEntry] = []
    for entry in entries:
        parts = by_transaction.get(entry.transaction_id)
        out.extend(_split_entry(entry, parts, inputs) if parts else [entry])
    return out
