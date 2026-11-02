"""Synthetic old-repo trees built in tmp_path; no real data."""

import textwrap
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from moneyflow.storage import db
from tools.import_old.__main__ import run
from tools.import_old.read import read_accounts, read_categories, read_overrides, read_raw, read_rules

_HEADER = "date,time,amount,currency,direction,mcc,description,comment,category,id"
_HEADER_UAH = "date,time,amount_uah,direction,mcc,description,comment,category,id"


def _accounts_yaml(*accounts: tuple[str, str, bool]) -> str:
    return yaml.safe_dump(
        {
            "accounts": [
                {"id": i, "name": f"name-{i}", "currency": c, "owner": "o", "role": "spending", "include": inc}
                for i, c, inc in accounts
            ]
        }
    )


def _row(tid: str, amount: str = "10.50", direction: str = "expense", day: int = 5) -> str:
    return f"2026-01-{day:02d},2026-01-{day:02d}T12:00:00+02:00,{amount},UAH,{direction},5411,shop,note,,{tid}"


def _old_repo(
    tmp_path: Path,
    *,
    accounts=(("acc1", "UAH", True),),
    rules: list | None = None,
    overrides: dict | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    data = tmp_path / "old" / "data"
    (data / "monobank_import" / "categorized").mkdir(parents=True)
    (data / "accounts.yaml").write_text(_accounts_yaml(*accounts))
    (data / "categories.yaml").write_text(
        yaml.safe_dump({"categories": [{"name": "food"}, {"name": "misc"}]})
    )
    (data / "rules.yaml").write_text(yaml.safe_dump({"rules": rules or []}))
    (data / "manual.yaml").write_text(yaml.safe_dump({"overrides": overrides or {}}))
    for name, body in (files or {}).items():
        (data / "monobank_import" / name).write_text(body)
    return tmp_path / "old"


def _csv(*rows: str, header: str = _HEADER) -> str:
    return "\n".join([header, *rows]) + "\n"


def test_rules_keep_file_order_as_ordinal(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        textwrap.dedent(
            """
            rules:
              - {name: second, priority: 10, match: {description_contains: a}, category: food}
              - {name: first, priority: 10, match: {mcc: 5411, amount_lt: 500}, category: misc, subcategory: x}
            """
        )
    )
    rules = read_rules(path)
    assert [(r.id, r.ordinal, r.priority, r.kind, r.active) for r in rules] == [
        ("second", 0, 10, "categorize", True),
        ("first", 1, 10, "categorize", True),
    ]
    assert rules[0].result == {"category": "food", "subcategory": None}
    assert rules[1].result == {"category": "misc", "subcategory": "x"}
    assert rules[1].match == {"mcc": 5411, "amount_lt": "500"}


def test_account_from_filename_and_currency_from_registry(tmp_path):
    repo = _old_repo(
        tmp_path,
        accounts=(("my_acc", "USD", True),),
        files={"my_acc_2026-01-01_2026-01-31.csv": _csv(_row("t1"))},
    )
    accounts = read_accounts(repo / "data" / "accounts.yaml")
    (t,) = read_raw(repo / "data" / "monobank_import", accounts)
    assert (t.account_id, t.currency) == ("my_acc", "USD")
    assert (t.amount, t.direction, t.mcc) == (Decimal("10.50"), "expense", 5411)
    assert t.date.isoformat() == "2026-01-05"
    assert t.time.isoformat() == "2026-01-05T10:00:00"  # naive UTC


def test_dedupe_last_file_wins_and_differing_copies_reported(tmp_path, capsys):
    repo = _old_repo(
        tmp_path,
        files={
            "acc1_2026-01-01_2026-01-10.csv": _csv(_row("t1", "1.00"), _row("t2", "2.00")),
            "acc1_2026-01-05_2026-01-31.csv": _csv(_row("t1", "9.00"), _row("t2", "2.00")),
        },
    )
    accounts = read_accounts(repo / "data" / "accounts.yaml")
    txs = read_raw(repo / "data" / "monobank_import", accounts)
    assert {t.id: t.amount for t in txs} == {"t1": Decimal("9.00"), "t2": Decimal("2.00")}
    out = capsys.readouterr().out
    assert "differing duplicates: 1" in out
    assert "t1" in out


def test_subdirectory_csvs_ignored(tmp_path):
    repo = _old_repo(tmp_path, files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"))})
    raw = repo / "data" / "monobank_import"
    (raw / "categorized" / "ghost_2026-01-01_2026-01-31.csv").write_text(_csv(_row("t9")))
    (raw / "README.md").write_text("notes")
    txs = read_raw(raw, read_accounts(repo / "data" / "accounts.yaml"))
    assert [t.id for t in txs] == ["t1"]


def test_unknown_account_file_raises(tmp_path):
    repo = _old_repo(tmp_path, files={"nobody_2026-01-01_2026-01-31.csv": _csv(_row("t1"))})
    with pytest.raises(ValueError, match="nobody"):
        read_raw(repo / "data" / "monobank_import", read_accounts(repo / "data" / "accounts.yaml"))


def test_amount_uah_header_is_read(tmp_path):
    plain = _csv(_row("t1", "123.40"))
    uah = _csv("2026-01-05,2026-01-05T12:00:00+02:00,123.40,expense,5411,shop,note,,t1", header=_HEADER_UAH)
    repo = _old_repo(tmp_path, files={"acc1_2026-01-01_2026-01-31.csv": plain})
    other = _old_repo(tmp_path / "x", files={"acc1_2026-01-01_2026-01-31.csv": uah})
    got = read_raw(repo / "data" / "monobank_import", read_accounts(repo / "data" / "accounts.yaml"))
    got_uah = read_raw(other / "data" / "monobank_import", read_accounts(other / "data" / "accounts.yaml"))
    assert got_uah == got
    assert got_uah[0].amount == Decimal("123.40")


def test_unknown_direction_raises_with_id(tmp_path):
    repo = _old_repo(
        tmp_path, files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("bad-id-7", direction="reimbursement"))}
    )
    with pytest.raises(ValueError, match="bad-id-7"):
        read_raw(repo / "data" / "monobank_import", read_accounts(repo / "data" / "accounts.yaml"))


def test_negative_amount_raises_with_id(tmp_path):
    repo = _old_repo(tmp_path, files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("neg-1", amount="-5.00"))})
    with pytest.raises(ValueError, match="neg-1"):
        read_raw(repo / "data" / "monobank_import", read_accounts(repo / "data" / "accounts.yaml"))


def test_categories_and_accounts_read(tmp_path):
    repo = _old_repo(tmp_path, accounts=(("a", "UAH", True), ("b", "USD", False)))
    assert read_categories(repo / "data" / "categories.yaml") == frozenset({"food", "misc"})
    accounts = read_accounts(repo / "data" / "accounts.yaml")
    assert [(a.id, a.currency, a.include) for a in accounts] == [("a", "UAH", True), ("b", "USD", False)]


def test_override_without_subcategory_imports_none(tmp_path):
    path = tmp_path / "manual.yaml"
    path.write_text(
        yaml.safe_dump(
            {"overrides": {"t1": {"category": "food", "note": "n1"}, "t2": {"category": "misc", "subcategory": "s", "note": "n2"}}}
        )
    )
    a, b = read_overrides(path)
    assert (a.id, a.transaction_id, a.kind, a.created_by) == ("override:t1", "t1", "category", "import")
    assert a.payload == {"category": "food", "subcategory": None, "note": "n1"}
    assert b.payload == {"category": "misc", "subcategory": "s", "note": "n2"}


def _db_repo(tmp_path, n_absent: int = 1):
    overrides = {"t1": {"category": "food", "note": "n"}, "t2": {"category": "misc", "subcategory": "s", "note": "n"}}
    for i in range(n_absent):
        overrides[f"manual-{i}"] = {"category": "food", "note": "n"}
    return _old_repo(
        tmp_path,
        rules=[{"name": "r1", "priority": 1, "match": {"description_contains": "shop"}, "category": "food"}],
        overrides=overrides,
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"), _row("t2", "3.00", "income"))},
    )


def test_override_for_absent_transaction_is_reported_not_imported(conn, tmp_path, capsys):
    repo = _db_repo(tmp_path)
    counts = run(repo, conn)
    out = capsys.readouterr().out
    assert "not imported: 1 overrides for transactions outside phase 0 input" in out
    assert "manual-0" in out
    assert counts["rulings"] == 2
    assert [r.transaction_id for r in db.load_inputs(conn).rulings] == ["t1", "t2"]


def test_imported_ruling_count_matches_overrides(conn, tmp_path, capsys):
    repo = _db_repo(tmp_path, n_absent=3)
    counts = run(repo, conn)
    out = capsys.readouterr().out
    overrides = read_overrides(repo / "data" / "manual.yaml")
    assert len(overrides) == 5
    assert "not imported: 3 overrides" in out
    assert counts["rulings"] == len(overrides) - 3


def test_reimport_is_idempotent_and_keeps_non_import_rows(conn, tmp_path):
    repo = _db_repo(tmp_path)
    first = run(repo, conn)
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('op1', 't1', 'category', '{\"category\": \"misc\", \"subcategory\": null}', 'operator')"
    )
    second = run(repo, conn)
    assert first == {"accounts": 1, "transactions": 2, "rules": 1, "rulings": 2, "categories": 2}
    assert second == {**first, "rulings": 3}
    assert {r.id for r in db.load_inputs(conn).rulings} == {"override:t1", "override:t2", "op1"}
