"""Exclude: a hand ruling beats every signal, then the mechanisms in a fixed order."""

from collections.abc import Callable
from dataclasses import replace

from moneyflow.core.cash import cash
from moneyflow.core.cross_owner import coverage, cross_owner
from moneyflow.core.internal import internal_pairs
from moneyflow.core.kinds.exclude import apply_rule, apply_ruling
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Rule, Transaction
from moneyflow.core.owners import is_budget_owner_account, owner_of


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


def _excluded(entry: LedgerEntry, reason: str, decided_by: str) -> LedgerEntry:
    return replace(entry, status="excluded", reason=reason, decided_by=decided_by)


def _other_owner_income(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A9: income on an account whose owner is not the budget owner is not the budget's income."""
    income = {t.id for t in inputs.transactions if t.direction == "income"}
    reason = "other-owner-income"
    return [
        _excluded(e, reason, f"core:{reason}")
        if e.status == "unresolved" and e.transaction_id in income and not is_budget_owner_account(e.account_id, inputs)
        else e
        for e in entries
    ]


def _partner(tx: Transaction, inputs: Inputs, state: dict[str, LedgerEntry]) -> Transaction | None:
    """An open expense with the same date, amount and currency on an account of the same owner."""
    owner = owner_of(tx.account_id, inputs)
    for other in inputs.transactions:
        if other.direction != "expense" or other.id == tx.id or state[other.id].status != "unresolved":
            continue
        if (other.date, other.amount, other.currency) == (tx.date, tx.amount, tx.currency) and owner_of(other.account_id, inputs) == owner:
            return other
    return None


def _first_match(tx: Transaction, rules: list[Rule], partner: Callable[[Transaction], Transaction | None]) -> tuple[Rule, str] | None:
    for rule in rules:
        reason = apply_rule(rule, tx, partner)
        if reason is not None:
            return rule, reason
    return None


def _exclude_rules(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A10/A11/A14: the first active exclude rule by (priority, ordinal) that matches wins."""
    rules = sorted((r for r in inputs.rules if r.active and r.kind == "exclude"), key=lambda r: (r.priority, r.ordinal))
    by_id = {t.id: t for t in inputs.transactions}
    state = {e.transaction_id: e for e in entries}

    def partner(tx: Transaction) -> Transaction | None:
        return _partner(tx, inputs, state)

    for entry in entries:
        if state[entry.transaction_id].status != "unresolved":
            continue
        tx = by_id[entry.transaction_id]
        hit = _first_match(tx, rules, partner)
        if hit is None:
            continue
        rule, reason = hit
        for target in (tx, partner(tx)) if rule.result.get("paired") else (tx,):
            assert target is not None
            state[target.id] = _excluded(state[target.id], reason, f"rule:{rule.id}")
    return [state[e.transaction_id] for e in entries]


def _internal(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    """A12: both legs of an internal transfer are excluded."""
    paired = internal_pairs(entries, inputs)
    reason = "internal-transfer"
    return [_excluded(e, reason, f"core:{reason}") if e.transaction_id in paired else e for e in entries]


def exclude(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    entries = _exclude_rulings(entries, inputs)
    entries = cash(entries, inputs)
    entries = _cross_owner(entries, inputs)  # A6/A7/A8
    entries = _other_owner_income(entries, inputs)
    entries = _exclude_rules(entries, inputs)
    return _internal(entries, inputs)
