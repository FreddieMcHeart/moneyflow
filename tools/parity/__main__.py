"""python -m tools.parity --old-repo PATH --db URL"""

import argparse
import csv
from pathlib import Path

import yaml

from moneyflow.storage import db
from tools.parity.compare import ExcludedParityResult, ParityResult, compare, compare_excluded


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tools.parity")
    parser.add_argument("--old-repo", required=True, type=Path)
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    data = args.old_repo / "data"
    with db.connect(args.db) as conn:
        new = db.load_ledger(conn)
        inputs = db.load_inputs(conn)
    raw = set(_raw_ids(data)) | _manual_ids(inputs)
    result = compare(_old_ledger(data), _rule_names(data), raw, new)
    excluded = compare_excluded(_read_csv(data / "excluded.csv"), raw, new, _not_included_ids(inputs))
    report(result)
    report_excluded(excluded)
    # Old rows outside budget cycles are not in data/ledger, so neither pass compares them.
    print(f"old rows outside budget cycles: {len(_read_csv(data / 'outside_cycles.csv'))} (not value-compared)")
    raise SystemExit(exit_code(result, excluded))


if __name__ == "__main__":
    main()
