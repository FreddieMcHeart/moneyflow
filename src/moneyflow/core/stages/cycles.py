"""Cycles stage (A24): give every counted row its budget month; a row before the first cycle is outside_cycle.

Outside-cycle beats unresolved, as in the old pipeline: a leftover row is never an open question.
Excluded rows carry no budget month. With no cycle at all the stage leaves the entries as they are.
"""

from dataclasses import replace

from moneyflow.core.cycles import build_cycles, cycle_of
from moneyflow.core.model import Inputs, LedgerEntry


def cycles(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    found = build_cycles(entries, inputs)
    if not found:
        return entries
    out: list[LedgerEntry] = []
    for e in entries:
        if e.status not in ("kept", "unresolved"):
            out.append(e)
            continue
        cycle = cycle_of(found, e.date)
        out.append(replace(e, status="outside_cycle") if cycle is None else replace(e, budget_month=cycle.budget_month))
    return out
