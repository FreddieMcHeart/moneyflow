"""Parity of the new ledger against the old one, for rows decided by rules and overrides."""

from dataclasses import dataclass
from decimal import Decimal

from moneyflow.core.model import Ledger, LedgerEntry

_OVERRIDE = "override:manual"


@dataclass(frozen=True)
class ParityResult:
    population: int
    compared: int
    differ: list[tuple[str, str]]  # (transaction_id, field)
    missing: list[str]
    out_of_scope: list[str]


def _signed(old: dict[str, str]) -> Decimal | None:
    amount = Decimal(old["amount"])
    return {"income": amount, "expense": -amount}.get(old["direction"])


def _decision_ok(rule: str, decided_by: str | None) -> bool:
    if decided_by is None:
        return False
    if rule == _OVERRIDE:
        return decided_by.startswith("ruling:")
    return decided_by == f"rule:{rule}"


def _field_diffs(old: dict[str, str], new: LedgerEntry) -> list[str]:
    signed = _signed(old)
    if signed is None:
        return ["direction"]
    checks = {
        "category": old["category"] == new.category,
        "subcategory": (old["subcategory"] or None) == new.subcategory,
        "currency": old["currency"] == new.currency,
        "amount": signed == new.amount,
        "decided_by": _decision_ok(old["rule"], new.decided_by),
    }
    return [field for field, ok in checks.items() if not ok]


def compare(old_rows: list[dict[str, str]], rule_names: set[str], raw_ids: set[str], new: Ledger) -> ParityResult:
    first_parts = {e.transaction_id: e for e in new if e.part == 0}
    population = [r for r in old_rows if r["rule"] in rule_names or r["rule"] == _OVERRIDE]
    in_scope = [r for r in population if r["id"] in raw_ids]
    differ = [
        (r["id"], field)
        for r in in_scope
        if r["id"] in first_parts
        for field in _field_diffs(r, first_parts[r["id"]])
    ]
    return ParityResult(
        population=len(population),
        compared=len(in_scope),
        differ=differ,
        missing=sorted(raw_ids - first_parts.keys()),
        out_of_scope=[r["id"] for r in population if r["id"] not in raw_ids],
    )


NEXT_SLICE_REASONS = frozenset({"split", "credit-principal"})
_NOT_INCLUDED = "account-not-included"
_EXCLUDED_PREFIX = "excluded:"


@dataclass(frozen=True)
class ExcludedParityResult:
    population: int  # old excluded rows whose id is a raw id and whose reason is in this slice
    compared: int
    differ: list[tuple[str, str, str]]  # (transaction_id, old reason, new reason or "kept" or "absent")
    old_kept: int  # raw ids minus every old excluded id
    old_kept_excluded: list[tuple[str, str]]  # (transaction_id, new reason)
    next_slice: dict[str, int]  # old rows per next-slice reason
    next_slice_excluded: list[tuple[str, str, str]]  # (base id, old reason, new reason)
    derived: int  # old excluded rows whose id has '#'
    split_pending: int
    split_pending_excluded: list[str]
    not_included_mismatch: list[str]  # symmetric difference of new account-not-included ids and the allowlist


def _old_reason(row: dict[str, str]) -> str:
    rule = row["rule"]
    return rule[len(_EXCLUDED_PREFIX):] if rule.startswith(_EXCLUDED_PREFIX) else rule


def _new_reason(excluded: dict[str, LedgerEntry], tid: str) -> str:
    return excluded[tid].reason or "none" if tid in excluded else "kept"


def compare_excluded(
    old_excluded: list[dict[str, str]],
    raw_ids: set[str],
    new: Ledger,
    not_included_ids: set[str],
    split_pending_ids: set[str],
) -> ExcludedParityResult:
    excluded = {e.transaction_id: e for e in new if e.status == "excluded"}
    old = [(r["id"], _old_reason(r)) for r in old_excluded]
    old_ids = {tid for tid, _ in old}
    scope = [(tid, reason) for tid, reason in old if tid in raw_ids and reason not in NEXT_SLICE_REASONS]
    differ = [
        (tid, reason, _new_reason(excluded, tid))
        for tid, reason in scope
        if _new_reason(excluded, tid) != reason
    ]
    next_rows = [(tid.split("#")[0], reason) for tid, reason in old if reason in NEXT_SLICE_REASONS]
    next_ids = {tid for tid, _ in next_rows}
    next_slice: dict[str, int] = {}
    for _, reason in next_rows:
        next_slice[reason] = next_slice.get(reason, 0) + 1
    old_kept = raw_ids - old_ids
    stray = sorted(
        (tid, excluded[tid].reason or "none")
        for tid in old_kept - next_ids
        if tid in excluded and not (excluded[tid].reason == _NOT_INCLUDED and tid in not_included_ids)
    )
    new_not_included = {tid for tid, e in excluded.items() if e.reason == _NOT_INCLUDED}
    return ExcludedParityResult(
        population=len(scope),
        compared=len(scope),
        differ=differ,
        old_kept=len(old_kept),
        old_kept_excluded=stray,
        next_slice=next_slice,
        next_slice_excluded=sorted((tid, reason, excluded[tid].reason or "none")
                                   for tid, reason in next_rows if tid in excluded),
        derived=sum(1 for tid, _ in old if "#" in tid),
        split_pending=len(split_pending_ids),
        split_pending_excluded=sorted(tid for tid in split_pending_ids if tid in excluded),
        not_included_mismatch=sorted(new_not_included ^ not_included_ids),
    )
