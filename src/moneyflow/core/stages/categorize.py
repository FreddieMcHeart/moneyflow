"""Categorize: a ruling beats a rule; the first matching rule by (priority, ordinal) wins."""

from collections.abc import Callable
from dataclasses import replace

from moneyflow.core.kinds import cancellation, investment_withdrawal
from moneyflow.core.kinds.categorize import apply_rule, apply_ruling
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Rule, Transaction

Decision = tuple[str, str | None, str]  # category, subcategory, decided_by

# A reclassified row is matched against the categorize rules as the purchase or deposit it reverses.
SHADOWS: dict[str, Callable[[Rule, Transaction], Transaction]] = {
    "cancellation": cancellation.shadow,
    "investment_withdrawal": investment_withdrawal.shadow,
}


def _shadow_of(entry: LedgerEntry, tx: Transaction, by_id: dict[str, Rule]) -> Transaction:
    rule = by_id.get(entry.via[5:]) if entry.via is not None and entry.via.startswith("rule:") else None
    return SHADOWS[rule.kind](rule, tx) if rule is not None and rule.kind in SHADOWS else tx


def _ruling_decisions(inputs: Inputs) -> dict[str, Decision]:
    out: dict[str, Decision] = {}
    for ruling in (r for r in inputs.rulings if r.kind == "category"):
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


def _settle(entry: LedgerEntry, decision: Decision | None, inputs: Inputs) -> LedgerEntry:
    if decision is not None:
        return _apply(entry, decision, inputs)
    # a reclassified row no rule can place is open for a reason, not silently uncategorised
    return replace(entry, reason="unmatched") if entry.status == "unresolved" and entry.via is not None else entry


def categorize(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    txs = {t.id: t for t in inputs.transactions}
    rulings = _ruling_decisions(inputs)
    rules = _ordered_rules(inputs)
    by_id = {r.id: r for r in inputs.rules}
    out: list[LedgerEntry] = []
    for entry in entries:
        if entry.status != "unresolved" and entry.transaction_id in rulings:
            raise BuildRefused(f"ruling on an {entry.status} entry", entry.transaction_id)
        decision = None
        if entry.status == "unresolved":
            decision = _decide(_shadow_of(entry, txs[entry.transaction_id], by_id), rulings, rules)
        out.append(_settle(entry, decision, inputs))
    return out
