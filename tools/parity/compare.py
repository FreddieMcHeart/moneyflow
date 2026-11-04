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


_SIGN = {"income": 1, "reimbursement": 1, "expense": -1}


def _signed(old: dict[str, str], field: str = "amount") -> Decimal | None:
    sign = _SIGN.get(old["direction"])
    if sign is None or (field != "amount" and not old[field]):
        return None
    return sign * Decimal(old[field])


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


# --- cycles, UAH amounts, outside-cycle rows, totals and month summaries (0b.3) ---

_KOPECK = Decimal("0.01")
_ABSENT = "absent"


def _kopecks(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_KOPECK)


def compare_cycle_fields(old_rows: list[dict[str, str]], new: Ledger) -> list[tuple[str, str]]:
    """(id, field) for every old ledger row whose budget month or UAH amount differs from its new entry."""
    first = {e.transaction_id: e for e in new if e.part == 0}
    differ: list[tuple[str, str]] = []
    for row in old_rows:
        entry = first.get(row["id"])
        if entry is None:
            continue
        if (row["budget_month"] or None) != entry.budget_month:
            differ.append((row["id"], "budget_month"))
        if _kopecks(_signed(row, "amount_uah")) != _kopecks(entry.amount_uah):
            differ.append((row["id"], "amount_uah"))
    return differ


@dataclass(frozen=True)
class OutsideParityResult:
    population: int  # rows in the old outside_cycles file
    compared: int  # of those, rows the new ledger also holds as outside_cycle
    differ: list[tuple[str, str]]  # (id, field); field is "absent", "status", "extra" or a value field


def _outside_diffs(old: dict[str, str], entry: LedgerEntry) -> list[str]:
    checks = {
        "category": (old["category"] or None) == entry.category,
        "subcategory": (old["subcategory"] or None) == entry.subcategory,
        "amount": _signed(old) == entry.amount,
    }
    return [field for field, ok in checks.items() if not ok]


def compare_outside(old_outside: list[dict[str, str]], new: Ledger) -> OutsideParityResult:
    first = {e.transaction_id: e for e in new if e.part == 0}
    differ: list[tuple[str, str]] = []
    compared = 0
    for row in old_outside:
        entry = first.get(row["id"])
        if entry is None:
            differ.append((row["id"], _ABSENT))
        elif entry.status != "outside_cycle":
            differ.append((row["id"], "status"))
        else:
            compared += 1
            differ += [(row["id"], field) for field in _outside_diffs(row, entry)]
    old_ids = {row["id"] for row in old_outside}
    extra = sorted(tid for tid, e in first.items() if e.status == "outside_cycle" and tid not in old_ids)
    return OutsideParityResult(len(old_outside), compared, differ + [(tid, "extra") for tid in extra])


Totals = dict[tuple[str, str | None, str | None, str], tuple[Decimal | None, Decimal | None]]


def _add(total: Decimal | None, value: Decimal | None) -> Decimal | None:
    return None if total is None or value is None else total + value


def _accumulate(totals: Totals, key: tuple, amount: Decimal | None, uah: Decimal | None) -> None:
    have_amount, have_uah = totals.get(key, (Decimal(0), Decimal(0)))
    totals[key] = (_add(have_amount, amount), _add(have_uah, uah))


def _old_totals(old_rows: list[dict[str, str]]) -> Totals:
    totals: Totals = {}
    for row in old_rows:
        if row["budget_month"] and row["category"]:
            key = (row["budget_month"], row["category"], row["subcategory"] or None, row["currency"])
            _accumulate(totals, key, _signed(row), _signed(row, "amount_uah"))
    return totals


def _new_totals(new: Ledger) -> Totals:
    totals: Totals = {}
    for e in new:
        if e.status == "kept":
            _accumulate(totals, (e.budget_month, e.category, e.subcategory, e.currency), e.amount, e.amount_uah)
    return totals


def _cell(totals: Totals, key: tuple, index: int) -> Decimal | str | None:
    if key not in totals:
        return _ABSENT
    return _kopecks(totals[key][index])


def _differs(old: Decimal | str | None, new: Decimal | str | None) -> bool:
    return old is None or new is None or old != new  # an unconverted (None) total never matches


def compare_totals(old_rows: list[dict[str, str]], new: Ledger) -> list[tuple[tuple, str]]:
    """((budget_month, category, subcategory, currency), field) for every kept-row total that differs."""
    old, fresh = _old_totals(old_rows), _new_totals(new)
    return [
        (key, field)
        for key in sorted(old.keys() | fresh.keys(), key=str)
        for index, field in enumerate(("amount", "amount_uah"))
        if _differs(_cell(old, key, index), _cell(fresh, key, index))
    ]


def _total_actual(new_value):
    return {currency: -amount for currency, amount in new_value.items()}  # old counts spending positive


_SUMMARY_FIELDS = (
    "income", "total_actual", "cycle_start", "cycle_end", "cycle_open", "days_total", "days_left", "cash_in_hand",
)


def _summary_value(summary, field: str, *, new: bool):
    value = getattr(summary, field)
    return _total_actual(value) if new and field == "total_actual" else value


def compare_summaries(old: list, new: list) -> list[tuple[str, str]]:
    """(budget_month, field) for every month summary field that differs; a month on one side only is "absent"."""
    old_by = {s.budget_month: s for s in old}
    new_by = {s.budget_month: s for s in new}
    differ: list[tuple[str, str]] = []
    for month in sorted(old_by.keys() | new_by.keys()):
        if month not in old_by or month not in new_by:
            differ.append((month, _ABSENT))
            continue
        differ += [
            (month, field)
            for field in _SUMMARY_FIELDS
            if _summary_value(old_by[month], field, new=False) != _summary_value(new_by[month], field, new=True)
        ]
    return differ


@dataclass(frozen=True)
class CycleLine:
    budget_month: str | None  # None: differences that belong to no known cycle
    open: bool
    rows: int
    totals: int
    summary: int

    def text(self) -> str:
        month = self.budget_month or "(no cycle)"
        return f"{month} rows differ {self.rows}, totals differ {self.totals}, summary differ {self.summary}"

    def clean(self) -> bool:
        return self.open or not (self.rows or self.totals or self.summary)


def _count(pairs, month_of) -> dict[str | None, int]:
    counts: dict[str | None, int] = {}
    for pair in pairs:
        month = month_of(pair)
        counts[month] = counts.get(month, 0) + 1
    return counts


def cycle_lines(
    old_summaries: list,
    old_rows: list[dict[str, str]],
    row_differ: list[tuple[str, str]],
    totals_differ: list[tuple[tuple, str]],
    summary_differ: list[tuple[str, str]],
) -> list[CycleLine]:
    """One line per old cycle, in cycle order, plus a "(no cycle)" line when a difference has no cycle."""
    month_by_id = {row["id"].split("#")[0]: row["budget_month"] or None for row in old_rows}
    rows = _count(row_differ, lambda pair: month_by_id.get(pair[0].split("#")[0]))
    totals = _count(totals_differ, lambda pair: pair[0][0])
    summary = _count(summary_differ, lambda pair: pair[0])
    months = {s.budget_month for s in old_summaries}
    lines = [
        CycleLine(s.budget_month, s.cycle_open, rows.get(s.budget_month, 0), totals.get(s.budget_month, 0),
                  summary.get(s.budget_month, 0))
        for s in old_summaries
    ]
    stray = [m for m in {*rows, *totals, *summary} if m not in months]
    if stray:
        count = lambda by: sum(by.get(m, 0) for m in stray)  # noqa: E731
        lines.append(CycleLine(None, False, count(rows), count(totals), count(summary)))
    return lines


@dataclass(frozen=True)
class CycleParity:
    outside: OutsideParityResult
    lines: list[CycleLine]


def cycles_clean(lines: list[CycleLine], outside: OutsideParityResult) -> bool:
    """Every closed cycle clean and the outside-cycle rows matching; the open cycle is reported, not gated."""
    return bool(lines) and not outside.differ and all(line.clean() for line in lines)
