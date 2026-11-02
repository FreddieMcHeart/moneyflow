"""Categorize: a ruling beats a rule; the first matching rule by (priority, ordinal) wins."""

from dataclasses import replace

from moneyflow.core.kinds.categorize import apply_rule, apply_ruling
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Rule, Transaction

Decision = tuple[str, str | None, str]  # category, subcategory, decided_by


def _ruling_decisions(inputs: Inputs) -> dict[str, Decision]:
    out: dict[str, Decision] = {}
    for ruling in inputs.rulings:
        if ruling.transaction_id in out:
            raise BuildRefused("more than one ruling", ruling.transaction_id)
        category, subcategory = apply_ruling(ruling)
        out[ruling.transaction_id] = (category, subcategory, f"ruling:{ruling.id}")
    return out


def _ordered_rules(inputs: Inputs) -> list[Rule]:
    active = (r for r in inputs.rules if r.active and r.kind == "categorize")
    return sorted(active, key=lambda r: (r.priority, r.ordinal))


def _decide(tx: Transaction, rulings: dict[str, Decision], rules: list[Rule]) -> Decision | None:
    if tx.id in rulings:
        return rulings[tx.id]
    for rule in rules:
        try:
            result = apply_rule(rule, tx)
        except (ValueError, ArithmeticError) as broken:  # decimal.InvalidOperation is an ArithmeticError
            raise BuildRefused(f"rule {rule.id}: {broken}", tx.id) from broken
        if result is not None:
            return result[0], result[1], f"rule:{rule.id}"
    return None


def _apply(entry: LedgerEntry, decision: Decision, inputs: Inputs) -> LedgerEntry:
    category, subcategory, decided_by = decision
    if category not in inputs.categories:
        raise BuildRefused(f"unknown category {category}", entry.transaction_id)
    return replace(entry, category=category, subcategory=subcategory, status="kept", decided_by=decided_by)


def categorize(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    txs = {t.id: t for t in inputs.transactions}
    rulings = _ruling_decisions(inputs)
    rules = _ordered_rules(inputs)
    out: list[LedgerEntry] = []
    for entry in entries:
        if entry.status != "unresolved" and entry.transaction_id in rulings:
            raise BuildRefused(f"ruling on an {entry.status} entry", entry.transaction_id)
        decision = _decide(txs[entry.transaction_id], rulings, rules) if entry.status == "unresolved" else None
        out.append(entry if decision is None else _apply(entry, decision, inputs))
    return out
