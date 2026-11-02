"""python -m tools.parity --old-repo PATH --db URL"""

import argparse
import csv
from pathlib import Path

import yaml

from moneyflow.storage import db
from tools.parity.compare import ParityResult, compare


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


def exit_code(result: ParityResult) -> int:
    clean = not result.differ and not result.missing
    return 0 if clean and result.population > 0 and result.compared > 0 else 1


def report(result: ParityResult) -> None:
    print(f"population: {result.population}")
    print(f"compared: {result.compared}")
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
    result = compare(_old_ledger(data), _rule_names(data), _raw_ids(data), new)
    report(result)
    raise SystemExit(exit_code(result))


if __name__ == "__main__":
    main()
