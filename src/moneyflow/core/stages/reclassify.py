"""Reclassify: a kept-or-open row that is not new spending or income becomes a reimbursement.

The steps run in a fixed order; a row a step changed (`via` set) is skipped by the later ones.
"""

from collections.abc import Callable
from dataclasses import replace

from moneyflow.core.kinds import cancellation, debt, investment_withdrawal, person_return, reimbursement
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Rule, Ruling, Transaction

Step = Callable[[list[LedgerEntry], Inputs], list[LedgerEntry]]
Change = Callable[[LedgerEntry, Rule, object, Inputs], LedgerEntry]


def _rules(inputs: Inputs, kind: str) -> list[Rule]:
    return sorted((r for r in inputs.rules if r.active and r.kind == kind), key=lambda r: (r.priority, r.ordinal))


def _first_hit(rules: list[Rule], tx: Transaction, apply: Callable[[Rule, Transaction], object]) -> tuple[Rule, object] | None:
    for rule in rules:
        try:
            hit = apply(rule, tx)
        except (ValueError, ArithmeticError) as broken:
            raise BuildRefused(f"rule {rule.id}: {broken}", tx.id) from broken
        if hit:
            return rule, hit
    return None


def _flipped(entry: LedgerEntry, rule: Rule) -> LedgerEntry:
    return replace(entry, direction="reimbursement", via=f"rule:{rule.id}")


def _by_rule(
    entries: list[LedgerEntry],
    inputs: Inputs,
    kind: str,
    apply: Callable[[Rule, Transaction], object],
    change: Change,
    skip: frozenset[str] = frozenset(),
) -> list[LedgerEntry]:
    rules = _rules(inputs, kind)
    txs = {t.id: t for t in inputs.transactions}
    out: list[LedgerEntry] = []
    for entry in entries:
        found = None
        if entry.status == "unresolved" and entry.via is None and entry.transaction_id not in skip:
            found = _first_hit(rules, txs[entry.transaction_id], apply)
        out.append(entry if found is None else change(entry, found[0], found[1], inputs))
    return out


def _flip(entry: LedgerEntry, rule: Rule, hit: object, inputs: Inputs) -> LedgerEntry:
    return _flipped(entry, rule)


def _set_person(entry: LedgerEntry, rule: Rule, hit: object, inputs: Inputs) -> LedgerEntry:
    _person, category = hit  # type: ignore[misc]
    if category not in inputs.categories:
        raise BuildRefused(f"unknown category {category}", entry.transaction_id)
    return replace(_flipped(entry, rule), category=category, subcategory=None, status="kept", decided_by=f"rule:{rule.id}")


def _rulings(inputs: Inputs, kind: str) -> dict[str, Ruling]:
    out: dict[str, Ruling] = {}
    for ruling in (r for r in inputs.rulings if r.kind == kind):
        if ruling.transaction_id in out:
            raise BuildRefused(f"more than one {kind} ruling", ruling.transaction_id)
        out[ruling.transaction_id] = ruling
    return out


def _by_ruling(entries: list[LedgerEntry], rulings: dict[str, Ruling], change: Callable[[LedgerEntry, Ruling], LedgerEntry]) -> list[LedgerEntry]:
    out: list[LedgerEntry] = []
    for entry in entries:
        ruling = rulings.get(entry.transaction_id)
        if ruling is not None and (entry.status != "unresolved" or entry.via is not None):
            raise BuildRefused(f"{ruling.kind} ruling on an entry that is {entry.status} or already reclassified", entry.transaction_id)
        out.append(entry if ruling is None else change(entry, ruling))
    return out


def _booked(entry: LedgerEntry, ruling: Ruling, category: str, subcategory: str | None, direction: str, inputs: Inputs) -> LedgerEntry:
    if category not in inputs.categories:
        raise BuildRefused(f"unknown category {category}", entry.transaction_id)
    via = f"ruling:{ruling.id}"
    return replace(entry, direction=direction, category=category, subcategory=subcategory, status="kept", decided_by=via, via=via)  # type: ignore[arg-type]


def _reimbursements(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A18: the ruling names the category the money came back against."""

    def change(entry: LedgerEntry, ruling: Ruling) -> LedgerEntry:
        return _booked(entry, ruling, reimbursement.category(ruling), None, "reimbursement", inputs)

    return _by_ruling(entries, _rulings(inputs, "reimbursement"), change)


def _debts(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A19: Debts / the person's display; a repayment flips to a reimbursement."""
    people = {p.id: p for p in inputs.people}
    txs = {t.id: t for t in inputs.transactions}
    booked: list[debt.Booked] = []

    def change(entry: LedgerEntry, ruling: Ruling) -> LedgerEntry:
        person, kind = debt.parse(ruling, people)
        tx = txs[entry.transaction_id]
        debt.check_row(tx, kind)
        booked.append((person, kind, tx))
        return _booked(entry, ruling, debt.CATEGORY, person.display, "reimbursement" if kind == "repaid" else entry.direction, inputs)

    out = _by_ruling(entries, _rulings(inputs, "debt"), change)
    debt.check_balances(booked)
    return out


def _cancellations(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A20: the category comes from the merchant rule, in `categorize`'s shadow match."""
    return _by_rule(entries, inputs, "cancellation", cancellation.apply_rule, _flip)


def _person_returns(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A21: the rule names the category."""
    return _by_rule(entries, inputs, "person_return", person_return.apply_rule, _set_person)


def _investment_withdrawals(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A22: a row with a category ruling is left alone, the ruling decides what it is."""
    ruled = frozenset(r.transaction_id for r in inputs.rulings if r.kind == "category")
    return _by_rule(entries, inputs, "investment_withdrawal", investment_withdrawal.apply_rule, _flip, ruled)


STEPS: list[Step] = [_reimbursements, _debts, _cancellations, _person_returns, _investment_withdrawals]


def reclassify(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    for step in STEPS:
        entries = step(entries, inputs)
    return entries
