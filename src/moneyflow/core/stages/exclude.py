"""Exclude: a hand ruling beats every signal, then the mechanisms in a fixed order."""

from dataclasses import replace

from moneyflow.core.cash import cash
from moneyflow.core.cross_owner import coverage, cross_owner
from moneyflow.core.internal import internal
from moneyflow.core.kinds.exclude import apply_ruling
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry


def _exclude_rulings(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    decided: dict[str, tuple[str, str]] = {}
    for ruling in inputs.rulings:
        if ruling.kind != "exclude":
            continue
        if ruling.transaction_id in decided:
            raise BuildRefused("more than one exclude ruling", ruling.transaction_id)
        decided[ruling.transaction_id] = (apply_ruling(ruling), f"ruling:{ruling.id}")
    out: list[LedgerEntry] = []
    for entry in entries:
        if entry.transaction_id not in decided:
            out.append(entry)
            continue
        if entry.status != "unresolved":
            raise BuildRefused(f"exclude ruling on an {entry.status} entry", entry.transaction_id)
        reason, decided_by = decided[entry.transaction_id]
        out.append(replace(entry, status="excluded", reason=reason, decided_by=decided_by))
    return out


def _cross_owner(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    reasons = cross_owner(entries, inputs, coverage(inputs.transactions))
    return [
        replace(e, status="excluded", reason=reasons[e.transaction_id], decided_by=f"core:{reasons[e.transaction_id]}")
        if e.transaction_id in reasons
        else e
        for e in entries
    ]


def _other_owner_income(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """Slot for A9; Task 5 replaces it. Returns the entries unchanged."""
    return entries


def _exclude_rules(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """Slot for the A10/A11/A14 rules (kinds.exclude.apply_rule); Task 5 replaces it."""
    return entries


def exclude(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    entries = _exclude_rulings(entries, inputs)
    entries = cash(entries, inputs)
    entries = _cross_owner(entries, inputs)  # A6/A7/A8
    entries = _other_owner_income(entries, inputs)
    entries = _exclude_rules(entries, inputs)
    return internal(entries, inputs)
