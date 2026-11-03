"""Synthetic old-repo trees built in tmp_path; no real data."""

import textwrap
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db
from tools.import_old.__main__ import run
from tools.import_old.read import read_accounts, read_categories, read_overrides, read_raw, read_rules

_HEADER = "date,time,amount,currency,direction,mcc,description,comment,category,id"
_HEADER_UAH = "date,time,amount_uah,direction,mcc,description,comment,category,id"


_OWNERS = {"o": {"display": "Owner O", "counterparty_strings": ["o-card", "o-name"]}}

_CASH_SOURCE = """from datetime import date

CASH_CUTOVER = date(2026, 11, 1)
CASH_ACCOUNT = "cash-test"
"""

_EXCLUSIONS_SOURCE = """import re

_CASHBACK = re.compile(r"(кешбек|кешбэк|округленн?я\\s+балансу)", re.IGNORECASE)
_CREDIT_DRAWDOWN = re.compile(
    r"розстрочка\\s+на\\s+картку|кредит\\s+до\\s+завтра|кредит\\s+готівкою", re.IGNORECASE)
_EXCEPTED_INSTALMENT_IDS = frozenset({"inst-1"})
_INSTALMENT_DRAWDOWN = re.compile(r"розстрочка\\s+на\\s+картку", re.IGNORECASE)
_MILES = re.compile(r"виведення\\s+миль", re.IGNORECASE)
"""


def _accounts_yaml(*accounts: tuple[str, str, bool], owners: dict | None = None, budget_owner: str = "o") -> str:
    return yaml.safe_dump(
        {
            "budget_owner": budget_owner,
            "owners": owners or _OWNERS,
            "accounts": [
                {"id": i, "name": f"name-{i}", "currency": c, "owner": "o", "role": "spending", "include": inc}
                for i, c, inc in accounts
            ]
        }
    )


def _row(tid: str, amount: str = "10.50", direction: str = "expense", day: int = 5, currency: str = "UAH") -> str:
    return f"2026-01-{day:02d},2026-01-{day:02d}T12:00:00+02:00,{amount},{currency},{direction},5411,shop,note,,{tid}"


def _old_repo(
    tmp_path: Path,
    *,
    accounts=(("acc1", "UAH", True),),
    rules: list | None = None,
    overrides: dict | None = None,
    files: dict[str, str] | None = None,
    owners: dict | None = None,
    budget_owner: str = "o",
    manual: dict | None = None,
    exclusions_source: str = _EXCLUSIONS_SOURCE,
) -> Path:
    data = tmp_path / "old" / "data"
    (data / "monobank_import" / "categorized").mkdir(parents=True)
    finance = tmp_path / "old" / "scripts" / "python" / "finance"
    finance.mkdir(parents=True)
    (finance / "cash.py").write_text(_CASH_SOURCE)
    (finance / "exclusions.py").write_text(exclusions_source)
    (data / "accounts.yaml").write_text(_accounts_yaml(*accounts, owners=owners, budget_owner=budget_owner))
    (data / "categories.yaml").write_text(
        yaml.safe_dump({"categories": [{"name": "food"}, {"name": "misc"}]})
    )
    (data / "rules.yaml").write_text(yaml.safe_dump({"rules": rules or []}))
    (data / "manual.yaml").write_text(yaml.safe_dump({"overrides": overrides or {}, **(manual or {})}))
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
        files={"my_acc_2026-01-01_2026-01-31.csv": _csv(_row("t1", currency="USD"))},
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


def test_row_currency_differing_from_account_raises_with_id(tmp_path):
    """The old loader refuses this too: a USD row in a UAH account must never be summed as UAH."""
    repo = _old_repo(tmp_path, files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("usd-1", currency="USD"))})
    with pytest.raises(ValueError, match="usd-1"):
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
    assert first == {"owners": 1, "accounts": 1, "transactions": 2, "rules": 8, "rulings": 2, "categories": 2}
    assert second == {**first, "rulings": 3}
    assert {r.id for r in db.load_inputs(conn).rulings} == {"override:t1", "override:t2", "op1"}


def test_stored_transaction_gone_from_source_is_reported_not_deleted(conn, tmp_path, capsys):
    repo = _db_repo(tmp_path)
    run(repo, conn)
    (repo / "data" / "monobank_import" / "acc1_2026-01-01_2026-01-31.csv").write_text(_csv(_row("t1")))
    capsys.readouterr()
    counts = run(repo, conn)
    out = capsys.readouterr().out
    assert "not in source: 1 stored transactions\n  t2\n" in out
    assert counts["transactions"] == 2
    assert [t.id for t in db.load_inputs(conn).transactions] == ["t1", "t2"]


def test_stale_transaction_ruling_survives_import_cli(conn, tmp_path):
    repo = _db_repo(tmp_path)
    run(repo, conn)
    (repo / "data" / "monobank_import" / "acc1_2026-01-01_2026-01-31.csv").write_text(_csv(_row("t2", "3.00", "income")))
    run(repo, conn)
    assert {r.id for r in db.load_inputs(conn).rulings} == {"override:t1", "override:t2"}


def _times(tmp_path, time: str):
    row = f"2026-01-05,{time},10.50,UAH,expense,5411,shop,note,,t1"
    repo = _old_repo(tmp_path, files={"acc1_2026-01-01_2026-01-31.csv": _csv(row)})
    return read_raw(repo / "data" / "monobank_import", read_accounts(repo / "data" / "accounts.yaml"))


def test_naive_time_read_as_utc(tmp_path):
    (naive,) = _times(tmp_path / "n", "2026-01-05T12:00:00")
    (aware,) = _times(tmp_path / "a", "2026-01-05T14:00:00+02:00")
    assert naive.time == aware.time == datetime(2026, 1, 5, 12, 0, 0)


def test_unparsable_time_refuses(tmp_path):
    with pytest.raises(ValueError):
        _times(tmp_path, "not a time")


def _by_id(conn, kind: str) -> dict:
    inputs = db.load_inputs(conn)
    return {x.id: x for x in (inputs.rulings if kind == "rulings" else inputs.rules)}


def test_imports_owners_and_budget_owner(conn, tmp_path):
    owners = {
        "o": {"display": "Owner O", "counterparty_strings": ["o-card"]},
        "p": {"display": "Owner P", "counterparty_strings": ["p-card", "p-name"]},
    }
    repo = _old_repo(
        tmp_path, accounts=(("acc1", "UAH", True),), owners=owners, budget_owner="p",
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"))},
    )
    run(repo, conn)
    stored = {o.id: o for o in db.load_inputs(conn).owners}
    assert [(o.id, o.display, o.counterparty_strings, o.budget) for o in stored.values()] == [
        ("o", "Owner O", ("o-card",), False),
        ("p", "Owner P", ("p-card", "p-name"), True),
    ]


def test_imports_cash_from(conn, tmp_path):
    repo = _old_repo(
        tmp_path, accounts=(("acc1", "UAH", True), ("cash-test", "UAH", True)),
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"))},
    )
    run(repo, conn)
    assert {a.id: a.cash_from for a in db.load_inputs(conn).accounts} == {
        "acc1": None, "cash-test": date(2026, 11, 1),
    }


def _manual_repo(tmp_path, *, overrides=None, manual=None, rows=("t1", "t2", "t3")):
    return _old_repo(
        tmp_path, overrides=overrides, manual=manual,
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(*(_row(t) for t in rows))},
    )


def test_imports_unpaired_and_white_card_rulings(conn, tmp_path):
    repo = _manual_repo(
        tmp_path,
        overrides={"t3": {"category": "food", "note": "n"}},
        manual={"unpaired_transfers": {"t1": "note a"}, "unpaired_internal": {"t2": "note b"}, "white_card_spending": {"t3": "wc"}},
    )
    run(repo, conn)
    rulings = _by_id(conn, "rulings")
    assert rulings["exclude:cross-owner-unpaired:t1"].payload == {"reason": "cross-owner-unpaired", "note": "note a"}
    assert rulings["exclude:internal-transfer:t2"].payload == {"reason": "internal-transfer", "note": "note b"}
    assert (rulings["white_card:t3"].kind, dict(rulings["white_card:t3"].payload)) == ("white_card", {})


def test_white_card_with_override_gets_both_rulings_with_distinct_ids(conn, tmp_path):
    repo = _manual_repo(
        tmp_path, overrides={"t1": {"category": "food", "note": "n"}}, manual={"white_card_spending": {"t1": "wc"}}
    )
    run(repo, conn)
    stored = [(g.id, g.kind) for g in db.load_inputs(conn).rulings if g.transaction_id == "t1"]
    assert sorted(stored) == [("override:t1", "category"), ("white_card:t1", "white_card")]


_SPLIT = {"t1": {"note": "n", "parts": [{"amount": "2.00", "category": "food", "subcategory": "sub"}]}}


def test_white_card_split_without_override_gets_split_pending_ruling(conn, tmp_path, capsys):
    repo = _manual_repo(tmp_path, manual={"white_card_spending": {"t1": "wc"}, "splits": _SPLIT})
    run(repo, conn)
    rulings = _by_id(conn, "rulings")
    pending = rulings["split_pending:t1"]
    assert (pending.kind, pending.payload["category"], pending.payload["subcategory"]) == ("category", "food", "sub")
    assert "split-pending" in str(pending.payload["note"])
    assert "imported split_pending rulings: 1" in capsys.readouterr().out


def test_split_pending_leg_passes_a4_in_rebuild(conn, tmp_path):
    owners = {
        "o": {"display": "Owner O", "counterparty_strings": ["o-name"]},
        "p": {"display": "Owner P", "counterparty_strings": ["p-name"]},
    }
    leg = "2026-01-05,2026-01-05T12:00:00+02:00,30.00,UAH,expense,5411,to p-name,,,t1"
    other = "2026-01-05,2026-01-05T13:00:00+02:00,7.00,UAH,expense,5411,shop,,,t9"
    repo = _old_repo(
        tmp_path, accounts=(("acc1", "UAH", True), ("acc2", "UAH", True)), owners=owners,
        manual={"white_card_spending": {"t1": "wc"}, "splits": _SPLIT},
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(leg), "acc2_2026-01-01_2026-01-31.csv": _csv(other)},
    )
    (repo / "data" / "accounts.yaml").write_text(
        yaml.safe_dump({
            "budget_owner": "o", "owners": owners,
            "accounts": [
                {"id": "acc1", "name": "n1", "currency": "UAH", "owner": "o", "role": "spending", "include": True},
                {"id": "acc2", "name": "n2", "currency": "UAH", "owner": "p", "role": "spending", "include": True},
            ],
        })
    )
    run(repo, conn)
    entry = next(e for e in rebuild(conn) if e.transaction_id == "t1")
    assert (entry.status, entry.category, entry.decided_by) == ("kept", "food", "ruling:split_pending:t1")


def test_imports_instalment_exception_ruling(conn, tmp_path):
    repo = _manual_repo(tmp_path, rows=("inst-1", "t2"))
    run(repo, conn)
    ruling = _by_id(conn, "rulings")["exclude:credit-drawdown:inst-1"]
    assert (ruling.kind, ruling.payload["reason"]) == ("exclude", "credit-drawdown")


def test_imports_exclude_rules_per_alternative(conn, tmp_path, capsys):
    run(_manual_repo(tmp_path), conn)
    rules = {r.id: r for r in db.load_inputs(conn).rules if r.kind == "exclude"}
    assert sorted((r.id, r.match["description_contains"], r.result["reason"]) for r in rules.values()) == [
        ("import-exclude-cashback-roundup-1", "кешбек", "cashback-roundup"),
        ("import-exclude-cashback-roundup-2", "кешбэк", "cashback-roundup"),
        ("import-exclude-cashback-roundup-3", "округлення балансу", "cashback-roundup"),
        ("import-exclude-cashback-roundup-4", "округленя балансу", "cashback-roundup"),
        ("import-exclude-credit-drawdown-1", "кредит до завтра", "credit-drawdown"),
        ("import-exclude-credit-drawdown-2", "кредит готівкою", "credit-drawdown"),
        ("import-exclude-miles-to-charity-1", "виведення миль", "miles-to-charity"),
    ]
    assert "imported exclude rules: 7" in capsys.readouterr().out


def test_instalment_alternative_is_not_a_rule(conn, tmp_path):
    run(_manual_repo(tmp_path), conn)
    assert not [r for r in db.load_inputs(conn).rules if "розстрочка" in str(r.match.get("description_contains"))]


def test_optional_letter_alternative_expands_to_two_literals(conn, tmp_path):
    run(_manual_repo(tmp_path), conn)
    literals = {r.match["description_contains"] for r in db.load_inputs(conn).rules if r.kind == "exclude"}
    assert {"округлення балансу", "округленя балансу"} <= literals
    assert not any("?" in str(x) or "\\" in str(x) for x in literals)


def test_regex_metacharacter_alternative_refuses(conn, tmp_path):
    source = _EXCLUSIONS_SOURCE.replace("кешбек|", "кеш.бек|")
    repo = _old_repo(tmp_path, exclusions_source=source, files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"))})
    with pytest.raises(ValueError, match="metacharacter"):
        run(repo, conn)


def test_drawdown_rule_has_direction_income(conn, tmp_path):
    run(_manual_repo(tmp_path), conn)
    by_id = _by_id(conn, "rules")
    assert by_id["import-exclude-credit-drawdown-2"].match == {"description_contains": "кредит готівкою", "direction": "income"}
    assert by_id["import-exclude-miles-to-charity-1"].match["direction"] == "income"
    assert by_id["import-exclude-miles-to-charity-1"].result["paired"] is True
    assert "direction" not in by_id["import-exclude-cashback-roundup-1"].match


def test_expense_row_with_drawdown_text_not_excluded_after_import_and_rebuild(conn, tmp_path):
    drawdown = "2026-01-05,2026-01-05T12:00:00+02:00,{amount},UAH,{direction},5411,кредит готівкою,,,{tid}"
    repo = _old_repo(
        tmp_path,
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(
            drawdown.format(amount="2000.00", direction="income", tid="in1"),
            drawdown.format(amount="500.00", direction="expense", tid="ex1"),
        )},
    )
    run(repo, conn)
    entries = {e.transaction_id: e for e in rebuild(conn)}
    assert (entries["in1"].status, entries["in1"].reason, entries["in1"].decided_by) == (
        "excluded", "credit-drawdown", "rule:import-exclude-credit-drawdown-2",
    )
    assert entries["ex1"].status != "excluded"


def test_reimport_of_exclude_inputs_is_idempotent(conn, tmp_path):
    repo = _manual_repo(
        tmp_path,
        overrides={"t3": {"category": "food", "note": "n"}},
        manual={"unpaired_transfers": {"t1": "a"}, "white_card_spending": {"t3": "wc"}, "splits": _SPLIT},
    )
    first = run(repo, conn)
    stored = {(g.id, g.kind) for g in db.load_inputs(conn).rulings}
    second = run(repo, conn)
    assert second == first
    assert {(g.id, g.kind) for g in db.load_inputs(conn).rulings} == stored
