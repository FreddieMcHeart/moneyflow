"""python -m tools.parity --old-repo PATH --db URL"""

import argparse
import csv
import importlib
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml

from moneyflow.core.cycles import build_cycles
from moneyflow.core.reports import month_summaries
from moneyflow.storage import db
from tools.parity.compare import (
    CycleParity,
    ExcludedParityResult,
    ParityResult,
    compare,
    compare_cycle_fields,
    compare_excluded,
    compare_outside,
    compare_summaries,
    compare_totals,
    cycle_lines,
    cycles_clean,
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _old_ledger(data: Path) -> list[dict[str, str]]:
    return [row for path in sorted((data / "ledger").glob("*.csv")) for row in _read_csv(path)]


def _raw_ids(data: Path) -> set[str]:
    return {row["id"] for path in sorted((data / "monobank_import").glob("*.csv")) for row in _read_csv(path)}


def _rule_names(data: Path) -> set[str]:
    with open(data / "rules.yaml", encoding="utf-8") as handle:
        return {str(r["name"]) for r in yaml.safe_load(handle)["rules"]}


def exit_code(result: ParityResult, excluded: ExcludedParityResult) -> int:
    clean = not result.differ and not result.missing
    ok = clean and result.population > 0 and result.compared > 0
    return 0 if ok and excluded_clean(excluded) else 1


def excluded_clean(r: ExcludedParityResult) -> bool:
    problems = (r.differ, r.old_kept_excluded, r.not_included_mismatch)
    return r.compared > 0 and not any(problems)


def _manual_ids(inputs) -> set[str]:
    return {t.id for t in inputs.transactions if t.source == "manual"}


def _not_included_ids(inputs) -> set[str]:
    off = {a.id for a in inputs.accounts if not a.include}
    return {t.id for t in inputs.transactions if t.account_id in off}


def _tally(pairs: list[tuple[str, ...]], key) -> dict[tuple[str, ...], int]:
    out: dict[tuple[str, ...], int] = {}
    for row in pairs:
        out[key(row)] = out.get(key(row), 0) + 1
    return out


def report_excluded(r: ExcludedParityResult) -> None:
    print(f"excluded population: {r.population}")
    print(f"excluded compared: {r.compared}")
    print(f"excluded differ: {len(r.differ)}")
    for reason, n in sorted(_tally(r.differ, lambda d: (d[1],)).items()):
        print(f"  by old reason {reason[0]}: {n}")
    for (old, new), n in sorted(_tally(r.differ, lambda d: (d[1], d[2])).items()):
        print(f"  by pair {old} -> {new}: {n}")
    print(f"old kept (raw minus old excluded): {r.old_kept}")
    print(f"old kept but new excluded: {len(r.old_kept_excluded)}")
    for (reason,), n in sorted(_tally(r.old_kept_excluded, lambda d: (d[1],)).items()):
        print(f"  by new reason {reason}: {n}")
    print(f"derived ids (with #): {r.derived}")
    print(f"excluded out_of_scope: {len(r.out_of_scope)}")
    print(f"account-not-included mismatch: {len(r.not_included_mismatch)}")


def report(result: ParityResult) -> None:
    print(f"population: {result.population}")
    print(f"compared: {result.compared}")
    print(f"parts compared (base ids): {result.parts_compared}")
    print(f"differ: {len(result.differ)}")
    for tid, field in result.differ[:20]:
        print(f"  {tid} {field}")
    print(f"missing: {len(result.missing)}")
    print(f"out_of_scope: {len(result.out_of_scope)}")


def _load_old_modules(old_repo: Path) -> SimpleNamespace:
    """Import the old pure finance and notionsync modules read-only; importing them reads no file and calls nothing."""
    path = str(old_repo / "scripts" / "python")
    if path not in sys.path:
        sys.path.insert(0, path)
    load = importlib.import_module
    return SimpleNamespace(
        ledger=load("finance.ledger"), accounts=load("finance.accounts"), cycles=load("finance.cycles"),
        cash=load("finance.cash"), model=load("notionsync.model"),
    )


def _old_summaries(data: Path, old: SimpleNamespace, today: date) -> list:
    """The old month summaries from the old ledger CSV, wired as notion_sync.py does but in the row's own currency."""
    rows = old.ledger.load_ledger_dir(data / "ledger")
    accounts = old.accounts.load_accounts(data / "accounts.yaml")
    income = {a.id for a in accounts.values() if a.role == "income"}
    cycles = old.cycles.build_cycles(rows, old.cycles.load_attribution(data / "manual.yaml"), income)
    spends = [r for r in rows if r.account == old.cash.CASH_ACCOUNT]
    cash = old.cash.cash_balance(
        old.cash.load_cash_movements(data / "excluded.csv"), spends, cycles, old.cash.CASH_OPENING,
        old.cash.CASH_CUTOVER, today,
    )
    return old.model.month_summaries(rows, cycles, today, cash_in_hand=cash)


def _cycle_passes(old_repo, data, old_rows, new, inputs, row_differ) -> CycleParity:
    new_summaries = month_summaries(new, build_cycles(list(new), inputs), inputs)
    old_summaries = _old_summaries(data, _load_old_modules(old_repo), inputs.today)
    cycle_differ = compare_cycle_fields(old_rows, new)
    lines = cycle_lines(
        old_summaries, old_rows, row_differ + cycle_differ, compare_totals(old_rows, new),
        compare_summaries(old_summaries, new_summaries),
    )
    return CycleParity(compare_outside(_read_csv(data / "outside_cycles.csv"), new), lines)


def report_cycles(parity: CycleParity) -> None:
    outside = parity.outside
    print(f"outside cycle population: {outside.population}")
    print(f"outside cycle compared: {outside.compared}")
    print(f"outside cycle differ: {len(outside.differ)}")
    for tid, field in outside.differ[:20]:
        print(f"  {tid} {field}")
    for line in parity.lines:
        print(line.text())
    open_months = [line.budget_month for line in parity.lines if line.open]
    print(f"open cycle, reported and not gated: {', '.join(open_months) or 'none'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tools.parity")
    parser.add_argument("--old-repo", required=True, type=Path)
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    data = args.old_repo / "data"
    with db.connect(args.db) as conn:
        new = db.load_ledger(conn)
        inputs = db.load_inputs(conn, today=date.today())
    raw = set(_raw_ids(data)) | _manual_ids(inputs)
    old_rows = _old_ledger(data)
    result = compare(old_rows, _rule_names(data), raw, new)
    excluded = compare_excluded(_read_csv(data / "excluded.csv"), raw, new, _not_included_ids(inputs))
    report(result)
    report_excluded(excluded)
    cycles = _cycle_passes(args.old_repo, data, old_rows, new, inputs, result.differ)
    report_cycles(cycles)
    raise SystemExit(1 if exit_code(result, excluded) or not cycles_clean(cycles.lines, cycles.outside) else 0)


if __name__ == "__main__":
    main()
