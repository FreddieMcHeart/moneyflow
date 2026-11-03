"""The build: refuse stray rulings, then fold the stages in order."""

from collections.abc import Callable

from moneyflow.core.model import BuildRefused, Inputs, Ledger, LedgerEntry
from moneyflow.core.stages.arithmetic import arithmetic
from moneyflow.core.stages.categorize import categorize
from moneyflow.core.stages.exclude import exclude
from moneyflow.core.stages.exclude_late import exclude_late
from moneyflow.core.stages.ingest import ingest
from moneyflow.core.stages.reclassify import reclassify

Stage = Callable[[list[LedgerEntry], Inputs], list[LedgerEntry]]

STAGES: list[Stage] = [ingest, exclude, reclassify, categorize, exclude_late, arithmetic]


def build(inputs: Inputs) -> Ledger:
    known = {t.id for t in inputs.transactions}
    for ruling in inputs.rulings:
        if ruling.transaction_id not in known:
            raise BuildRefused("ruling on a transaction the input does not have", ruling.transaction_id)
    entries: list[LedgerEntry] = []
    for stage in STAGES:
        entries = stage(entries, inputs)
    return tuple(sorted(entries, key=lambda e: (e.date, e.transaction_id, e.part)))
