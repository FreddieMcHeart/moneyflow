"""Parity of the new ledger against the old one, for rows decided by rules, rulings and reclassification."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from moneyflow.core.model import Ledger, LedgerEntry

_OVERRIDE = "override:manual"
_RULING_PREFIXES = {
    "reimbursement:manual": "ruling:reimbursement:",
    "debt:manual": "ruling:debt:",
    "manual-expense": "ruling:manual:",
    "manual-income": "ruling:manual:",
}
_SHADOW_PREFIXES = ("refund:", "investment-withdrawal:")
_UNMATCHED = "unmatched"
_CREDIT_INTEREST = "credit-interest"


@dataclass(frozen=True)
class ParityResult:
    population: int
    compared: int
    differ: list[tuple[str, str]]  # (transaction_id, field); a parts mismatch is (base id, "parts")
    missing: list[str]
    out_of_scope: list[str]
    parts_compared: int  # base ids whose old parts were compared with the new parts


def _signed(old: dict[str, str]) -> Decimal | None:
    amount = Decimal(old["amount"])
    return {"income": amount, "reimbursement": amount, "expense": -amount}.get(old["direction"])


def _bucket(rule: str, rule_names: set[str]) -> str | None:
    if rule in rule_names:
        return "rule"
    if rule == _OVERRIDE:
        return "override"
    if rule in _RULING_PREFIXES:
        return "ruling"
    if rule.startswith(_SHADOW_PREFIXES):
        return "shadow"
    return "return" if rule.endswith("-return") else None


def _via_rule(new: LedgerEntry) -> bool:
    return new.via is not None and new.via.startswith("rule:")


def _shadow_ok(rule: str, new: LedgerEntry) -> bool:
    merchant_rule = rule.split(":", 1)[1]
    expected = None if merchant_rule == _UNMATCHED else f"rule:{merchant_rule}"
    return _via_rule(new) and new.decided_by == expected


def _ruling_ok(rule: str, new: LedgerEntry) -> bool:
    prefix = _RULING_PREFIXES[rule]
    if rule.startswith("manual-"):
        return new.decided_by is not None and new.decided_by.startswith(prefix)
    return new.via is not None and new.via == new.decided_by and new.via.startswith(prefix)


def _decision_ok(rule: str, bucket: str, new: LedgerEntry) -> bool:
    checks = {
        "rule": lambda: new.decided_by == f"rule:{rule}",
        "override": lambda: new.decided_by is not None and new.decided_by.startswith("ruling:"),
        "ruling": lambda: _ruling_ok(rule, new),
        "shadow": lambda: _shadow_ok(rule, new),
        "return": lambda: _via_rule(new) and new.decided_by == f"rule:{rule}",
    }
    return checks[bucket]()


def _field_diffs(old: dict[str, str], bucket: str, new: LedgerEntry) -> list[str]:
    signed = _signed(old)
    if signed is None:
        return ["direction"]
    checks = {
        "category": old["category"] == new.category,
        "subcategory": (old["subcategory"] or None) == new.subcategory,
        "currency": old["currency"] == new.currency,
        "amount": signed == new.amount,
        "decided_by": _decision_ok(old["rule"], bucket, new),
    }
    return [field for field, ok in checks.items() if not ok]


def _old_kind(rule: str) -> str | None:
    if rule == "split" or rule == _CREDIT_INTEREST:
        return rule
    if rule.startswith("split-remainder"):
        return "remainder:ruling" if "override" in rule else "remainder:rule"
    return None


def _new_kind(entry: LedgerEntry) -> str:
    by = entry.decided_by or ""
    if by.startswith("ruling:split:"):
        return "split"
    if by == f"core:{_CREDIT_INTEREST}":
        return _CREDIT_INTEREST
    for prefix in ("ruling:", "rule:"):
        if by.startswith(prefix):
            return f"remainder:{prefix[:-1]}"
    return "remainder:none"


def _old_parts(old_rows: list[dict[str, str]], raw_ids: set[str]) -> tuple[dict[str, Counter], list[str]]:
    parts: dict[str, Counter] = {}
    gone: list[str] = []
    for row in old_rows:
        kind = _old_kind(row["rule"])
        if kind is None:
            continue
        base = row["id"].split("#")[0]
        if base not in raw_ids:
            gone.append(row["id"])
            continue
        key = (_signed(row), row["category"], row["subcategory"] or None, row["currency"], kind)
        parts.setdefault(base, Counter())[key] += 1
    return parts, gone


def _new_parts(new: Ledger) -> dict[str, Counter]:
    parts: dict[str, Counter] = {}
    for e in new:
        if e.part > 0 and e.status == "kept":
            key = (e.amount, e.category, e.subcategory, e.currency, _new_kind(e))
            parts.setdefault(e.transaction_id, Counter())[key] += 1
    return parts


def _parts_pass(old_rows: list[dict[str, str]], raw_ids: set[str], new: Ledger) -> tuple[list[tuple[str, str]], int, list[str]]:
    old, gone = _old_parts(old_rows, raw_ids)
    fresh = _new_parts(new)
    differ = [(base, "parts") for base in sorted(old.keys() | fresh.keys()) if old.get(base) != fresh.get(base)]
    return differ, len(old), gone


def _in_main_pass(row: dict[str, str], rule_names: set[str]) -> str | None:
    return None if "#" in row["id"] else _bucket(row["rule"], rule_names)


def compare(old_rows: list[dict[str, str]], rule_names: set[str], raw_ids: set[str], new: Ledger) -> ParityResult:
    first_parts = {e.transaction_id: e for e in new if e.part == 0}
    buckets = [(r, _in_main_pass(r, rule_names)) for r in old_rows]
    population = [(r, b) for r, b in buckets if b is not None]
    in_scope = [(r, b) for r, b in population if r["id"] in raw_ids]
    differ = [
        (r["id"], field)
        for r, b in in_scope
        if r["id"] in first_parts
        for field in _field_diffs(r, b, first_parts[r["id"]])
    ]
    parts_differ, parts_compared, parts_gone = _parts_pass(old_rows, raw_ids, new)
    return ParityResult(
        population=len(population),
        compared=len(in_scope),
        differ=differ + parts_differ,
        missing=sorted(raw_ids - first_parts.keys()),
        out_of_scope=[r["id"] for r, _ in population if r["id"] not in raw_ids] + parts_gone,
        parts_compared=parts_compared,
    )


_NOT_INCLUDED = "account-not-included"
_EXCLUDED_PREFIX = "excluded:"


@dataclass(frozen=True)
class ExcludedParityResult:
    population: int  # old excluded rows whose base id is a raw id
    compared: int
    differ: list[tuple[str, str, str]]  # (old id, old reason, new reason or "kept" or "absent")
    old_kept: int  # raw ids minus the base ids of every old excluded row
    old_kept_excluded: list[tuple[str, str]]  # (transaction_id, new reason)
    derived: int  # old excluded rows whose id has '#'
    out_of_scope: list[str]  # old excluded ids whose base id is not a raw id
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
) -> ExcludedParityResult:
    excluded = {e.transaction_id: e for e in new if e.status == "excluded"}
    old = [(r["id"], _old_reason(r)) for r in old_excluded]
    scope = [(tid, reason) for tid, reason in old if tid.split("#")[0] in raw_ids]
    differ = [
        (tid, reason, _new_reason(excluded, tid.split("#")[0]))
        for tid, reason in scope
        if _new_reason(excluded, tid.split("#")[0]) != reason
    ]
    old_kept = raw_ids - {tid.split("#")[0] for tid, _ in old}
    stray = sorted(
        (tid, excluded[tid].reason or "none")
        for tid in old_kept
        if tid in excluded and not (excluded[tid].reason == _NOT_INCLUDED and tid in not_included_ids)
    )
    new_not_included = {tid for tid, e in excluded.items() if e.reason == _NOT_INCLUDED}
    return ExcludedParityResult(
        population=len(scope),
        compared=len(scope),
        differ=differ,
        old_kept=len(old_kept),
        old_kept_excluded=stray,
        derived=sum(1 for tid, _ in old if "#" in tid),
        out_of_scope=sorted(tid for tid, _ in old if tid.split("#")[0] not in raw_ids),
        not_included_mismatch=sorted(new_not_included ^ not_included_ids),
    )
