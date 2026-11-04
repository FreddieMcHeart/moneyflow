"""The build: refuse stray rulings, then fold the stages in order."""

from collections.abc import Callable

from moneyflow.core.cycles import build_cycles, outside_text
from moneyflow.core.model import BuildRefused, Inputs, Ledger, LedgerEntry
from moneyflow.core.stages.arithmetic import arithmetic
from moneyflow.core.stages.categorize import categorize
from moneyflow.core.stages.cycles import cycles
from moneyflow.core.stages.exclude import exclude
from moneyflow.core.stages.exclude_late import exclude_late
from moneyflow.core.stages.ingest import ingest
from moneyflow.core.stages.reclassify import reclassify
from moneyflow.core.stages.split import split

Stage = Callable[[list[LedgerEntry], Inputs], list[LedgerEntry]]

STAGES: list[Stage] = [ingest, exclude, reclassify, categorize, exclude_late, split, cycles, arithmetic]


def _refuse_manual_not_kept(inputs: Inputs, entries: list[LedgerEntry]) -> None:
    """A30/A31: a manual transaction exists to be counted, so one that ends excluded, unresolved or outside every cycle is an error."""
    kept = {e.transaction_id for e in entries if e.status == "kept"}
    outside = {e.transaction_id: e for e in entries if e.status == "outside_cycle"}
    for tx in inputs.transactions:
        if tx.source != "manual" or tx.id in kept:
            continue
        if tx.id in outside:
            where = outside_text(outside[tx.id], build_cycles(entries, inputs))
            raise BuildRefused(f"manual transaction {tx.id} is outside every budget cycle ({where})", tx.id)
        raise BuildRefused(f"manual transaction {tx.id} did not end kept", None)


def build(inputs: Inputs) -> Ledger:
    known = {t.id for t in inputs.transactions}
    for ruling in inputs.rulings:
        if ruling.transaction_id not in known:
            raise BuildRefused("ruling on a transaction the input does not have", ruling.transaction_id)
    entries: list[LedgerEntry] = []
    for stage in STAGES:
        entries = stage(entries, inputs)
    _refuse_manual_not_kept(inputs, entries)
    return tuple(sorted(entries, key=lambda e: (e.date, e.transaction_id, e.part)))
