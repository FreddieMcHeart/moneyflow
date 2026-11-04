"""Cycles stage (A24): give every counted row its budget month; a row before the first cycle is outside_cycle.

Outside-cycle beats unresolved, as in the old pipeline: a leftover row is never an open question.
Excluded rows carry no budget month. With no cycle at all the stage leaves the entries as they are.
Then the refusals that need cycles: a split part outside its parent's cycle, a split parent outside every
cycle, and a debt row outside every cycle.
"""

from dataclasses import replace

from moneyflow.core.cycles import Cycle, build_cycles, cycle_of, describe, outside_text
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry
from moneyflow.core.stages.split import REASON as SPLIT_REASON


def _assign(entries: list[LedgerEntry], found: tuple[Cycle, ...]) -> list[LedgerEntry]:
    out: list[LedgerEntry] = []
    for e in entries:
        if e.status not in ("kept", "unresolved"):
            out.append(e)
            continue
        cycle = cycle_of(found, e.date)
        out.append(replace(e, status="outside_cycle") if cycle is None else replace(e, budget_month=cycle.budget_month))
    return out


def _refuse_split(entries: list[LedgerEntry], found: tuple[Cycle, ...]) -> None:
    """Old `apply_splits`: a part stays in its parent's cycle (the lower bound holds for the open cycle too)."""
    parents = {e.transaction_id: e for e in entries if e.status == "excluded" and e.reason == SPLIT_REASON}
    home = {transaction_id: cycle_of(found, p.date) for transaction_id, p in parents.items()}
    for transaction_id in sorted(home):
        if home[transaction_id] is None:
            raise BuildRefused("falls outside every cycle and cannot be split", transaction_id)
    for e in entries:
        cycle = home.get(e.transaction_id)
        if cycle is not None and e.part >= 1 and cycle_of(found, e.date) != cycle:
            raise BuildRefused(f"part {e.part} dated {e.date}, outside the row's cycle {describe(cycle)}", e.transaction_id)


def _refuse_debt_outside(entries: list[LedgerEntry], inputs: Inputs, found: tuple[Cycle, ...]) -> None:
    debts = {r.transaction_id for r in inputs.rulings if r.kind == "debt"}
    for e in entries:
        if e.status == "outside_cycle" and e.transaction_id in debts:
            raise BuildRefused(f"debt on {e.transaction_id} is outside every budget cycle ({outside_text(e, found)})", e.transaction_id)


def cycles(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    found = build_cycles(entries, inputs)
    if not found:
        return entries
    _refuse_split(entries, found)
    out = _assign(entries, found)
    _refuse_debt_outside(out, inputs, found)
    return out
