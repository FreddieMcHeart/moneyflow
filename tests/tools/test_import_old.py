"""Synthetic old-repo trees built in tmp_path; no real data."""

import sys
import textwrap
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db
from tests.core._builders import TODAY
from tools.import_old.__main__ import main, run
from tools.import_old.read import (
    read_accounts,
    read_categories,
    read_overrides,
    read_owners,
    read_plan,
    read_raw,
    read_rules,
)

_HEADER = "date,time,amount,currency,direction,mcc,description,comment,category,id"
_HEADER_UAH = "date,time,amount_uah,direction,mcc,description,comment,category,id"


_OWNERS = {"o": {"display": "Owner O", "counterparty_strings": ["o-card", "o-name"]}}

_CASH_SOURCE = """from datetime import date
from decimal import Decimal

CASH_CUTOVER = date(2026, 11, 1)
CASH_OPENING = Decimal("250.50")
CASH_ACCOUNT = "cash-test"
"""

_FX_SOURCE = """FOP_USD_ACCOUNT = "fop-test"
"""

_FX_HEADER = "row_id,supersedes,date,rate,kind,method,source_ids,observed_at,note"

_REFUNDS_SOURCE = """REFUND_PREFIX = "Скасування."
"""

_INVESTMENT_SOURCE = """import re

INVESTMENT_WITHDRAWAL = re.compile(r"інжур", re.IGNORECASE)
"""

_PERSON_RETURNS_SOURCE = """PERSON_RETURNS: dict[str, tuple[str, str]] = {
    "From: alice": ("food", "alice-return"),
    "From: bob": ("misc", "bob-return"),
}
"""

_EXCLUSIONS_SOURCE = """import re

_REPAYMENT_PREFIX = "Погашення "

_CASHBACK = re.compile(r"(кешбек|кешбэк|округленн?я\\s+балансу)", re.IGNORECASE)
_CREDIT_DRAWDOWN = re.compile(
    r"розстрочка\\s+на\\s+картку|кредит\\s+до\\s+завтра|кредит\\s+готівкою", re.IGNORECASE)
_EXCEPTED_INSTALMENT_IDS = frozenset({"inst-1"})
_INSTALMENT_DRAWDOWN = re.compile(r"розстрочка\\s+на\\s+картку", re.IGNORECASE)
_MILES = re.compile(r"виведення\\s+миль", re.IGNORECASE)
"""


_OLD_MODULES = ("monobank_import", "notionsync", "planexpand")

_LOAD_ENV_SOURCE = """def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env
"""

_PLAN_PAYLOADS_SOURCE = """class Schema:
    @classmethod
    def load(cls, path):
        return cls()

    def database_id(self, name):
        return name
"""

_PLAN_CLIENT_SOURCE = """PAGES = {pages!r}


class NotionClient:
    def __init__(self, token):
        self.token = token

    def query_database(self, database_id):
        return list(PAGES.get(database_id, ()))
"""

_NOTION_MODEL_SOURCE = """NO_SUBCATEGORY = "<none>"
"""

_PLAN_READ_SOURCE = """from decimal import Decimal
from types import SimpleNamespace

ROWS = {rows!r}


def read_grid(client, schema):
    assert client.token == {token!r}
    return [
        SimpleNamespace(
            key=SimpleNamespace(month=m, category=c, subcategory=s, currency=cur),
            amount=a, source=src, handed_over=h, page_id="page-" + str(i), created_by=None,
        )
        for i, (m, c, s, cur, a, src, h) in enumerate(ROWS)
    ]
"""


@pytest.fixture(autouse=True)
def _isolated_old_modules(monkeypatch):
    """`read_plan` puts the fake old repo on sys.path and imports from it; undo both after each test."""
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    for name in [n for n in sys.modules if n.split(".")[0] in _OLD_MODULES]:
        del sys.modules[name]


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
    with_cash_account: bool = True,
    fx_rows: tuple[str, ...] = (),
) -> Path:
    if with_cash_account and "cash-test" not in {a[0] for a in accounts}:
        accounts = (*accounts, ("cash-test", "UAH", True))
    data = tmp_path / "old" / "data"
    (data / "monobank_import" / "categorized").mkdir(parents=True)
    finance = tmp_path / "old" / "scripts" / "python" / "finance"
    finance.mkdir(parents=True)
    (finance / "cash.py").write_text(_CASH_SOURCE)
    (finance / "fx.py").write_text(_FX_SOURCE)
    (finance.parent / "monobank_import.py").write_text(_LOAD_ENV_SOURCE)
    (data / "history").mkdir()
    (data / "history" / "usd_rate.csv").write_text("\n".join([_FX_HEADER, *fx_rows]) + "\n")
    (finance / "exclusions.py").write_text(exclusions_source)
    (finance / "refunds.py").write_text(_REFUNDS_SOURCE)
    (finance / "investment_withdrawals.py").write_text(_INVESTMENT_SOURCE)
    (finance / "person_returns.py").write_text(_PERSON_RETURNS_SOURCE)
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
    repo = _old_repo(tmp_path, accounts=(("a", "UAH", True), ("b", "USD", False)), with_cash_account=False)
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
    assert counts["rulings"].inserted == 2
    assert [r.transaction_id for r in db.load_inputs(conn, today=TODAY).rulings] == ["t1", "t2"]


def test_imported_ruling_count_matches_overrides(conn, tmp_path, capsys):
    repo = _db_repo(tmp_path, n_absent=3)
    counts = run(repo, conn)
    out = capsys.readouterr().out
    overrides = read_overrides(repo / "data" / "manual.yaml")
    assert len(overrides) == 5
    assert "not imported: 3 overrides" in out
    assert counts["rulings"].inserted == len(overrides) - 3


def test_reimport_is_idempotent_and_keeps_non_import_rows(conn, tmp_path):
    repo = _db_repo(tmp_path)
    first = run(repo, conn)
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('op1', 't1', 'category', '{\"category\": \"misc\", \"subcategory\": null}', 'operator')"
    )
    second = run(repo, conn)
    assert {t: c.inserted for t, c in first.items()} == {
        "owners": 1, "accounts": 2, "transactions": 2, "rules": 13, "rulings": 2, "categories": 2, "people": 0,
        "fx_rates": 0}
    assert second == {t: (0, 0, 0, c.inserted) for t, c in first.items()}  # the operator ruling is not counted
    assert {r.id for r in db.load_inputs(conn, today=TODAY).rulings} == {"override:t1", "override:t2", "op1"}


def test_stored_transaction_gone_from_source_is_reported_not_deleted(conn, tmp_path, capsys):
    repo = _db_repo(tmp_path)
    run(repo, conn)
    (repo / "data" / "monobank_import" / "acc1_2026-01-01_2026-01-31.csv").write_text(_csv(_row("t1")))
    capsys.readouterr()
    counts = run(repo, conn)
    out = capsys.readouterr().out
    assert "not in source: 1 stored transactions\n  t2\n" in out
    assert counts["transactions"] == (0, 0, 0, 1)  # t1 only; t2 is stale, untouched
    assert [t.id for t in db.load_inputs(conn, today=TODAY).transactions] == ["t1", "t2"]


def test_stale_transaction_ruling_survives_import_cli(conn, tmp_path):
    repo = _db_repo(tmp_path)
    run(repo, conn)
    (repo / "data" / "monobank_import" / "acc1_2026-01-01_2026-01-31.csv").write_text(_csv(_row("t2", "3.00", "income")))
    run(repo, conn)
    assert {r.id for r in db.load_inputs(conn, today=TODAY).rulings} == {"override:t1", "override:t2"}


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
    inputs = db.load_inputs(conn, today=TODAY)
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
    stored = {o.id: o for o in db.load_inputs(conn, today=TODAY).owners}
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
    assert {a.id: a.cash_from for a in db.load_inputs(conn, today=TODAY).accounts} == {
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
    stored = [(g.id, g.kind) for g in db.load_inputs(conn, today=TODAY).rulings if g.transaction_id == "t1"]
    assert sorted(stored) == [("override:t1", "category"), ("white_card:t1", "white_card")]


_SPLIT = {"t1": {"note": "n", "parts": [{"amount": "2.00", "category": "food", "subcategory": "sub"}]}}


def test_white_card_with_split_writes_no_split_pending(conn, tmp_path, capsys):
    repo = _manual_repo(tmp_path, manual={"white_card_spending": {"t1": "wc"}, "splits": _SPLIT})
    run(repo, conn)
    rulings = _by_id(conn, "rulings")
    assert not [i for i in rulings if i.startswith("split_pending")]
    assert (rulings["white_card:t1"].kind, rulings["split:t1:1"].kind) == ("white_card", "split")
    assert "split_pending" not in capsys.readouterr().out


def test_white_card_split_leg_builds_in_rebuild(conn, tmp_path):
    owners = {
        "o": {"display": "Owner O", "counterparty_strings": ["o-name"]},
        "p": {"display": "Owner P", "counterparty_strings": ["p-name"]},
    }
    leg = "2026-01-05,2026-01-05T12:00:00+02:00,30.00,UAH,expense,5411,to p-name,,,t1"
    other = "2026-01-05,2026-01-05T13:00:00+02:00,7.00,UAH,expense,5411,shop,,,t9"
    repo = _old_repo(
        tmp_path, accounts=(("acc1", "UAH", True), ("acc2", "UAH", True)), owners=owners,
        rules=[{"name": "to-p", "priority": 1, "match": {"description_contains": "to p-name"}, "category": "misc"}],
        manual={"white_card_spending": {"t1": "wc"}, "splits": _SPLIT},
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(leg), "acc2_2026-01-01_2026-01-31.csv": _csv(other)},
    )
    (repo / "data" / "accounts.yaml").write_text(
        yaml.safe_dump({
            "budget_owner": "o", "owners": owners,
            "accounts": [
                {"id": "acc1", "name": "n1", "currency": "UAH", "owner": "o", "role": "spending", "include": True},
                {"id": "acc2", "name": "n2", "currency": "UAH", "owner": "p", "role": "spending", "include": True},
                {"id": "cash-test", "name": "c", "currency": "UAH", "owner": "o", "role": "spending", "include": True},
            ],
        })
    )
    run(repo, conn)
    parts = sorted((e.part, e.status, e.category, e.amount) for e in rebuild(conn, today=TODAY) if e.transaction_id == "t1")
    assert parts == [
        (0, "excluded", "misc", Decimal("-30.00")),
        (1, "kept", "food", Decimal("-2.00")),
        (2, "kept", "misc", Decimal("-28.00")),
    ]


def test_split_becomes_split_rulings(conn, tmp_path):
    split = {"t1": {"note": "n", "parts": [
        {"amount": "2.00", "category": "food", "subcategory": "sub"},
        {"amount": "3.00", "category": "misc", "description": "lunch", "date": "2026-01-06"},
    ]}}
    run(_manual_repo(tmp_path, manual={"splits": split}), conn)
    rulings = _by_id(conn, "rulings")
    assert (rulings["split:t1:1"].kind, dict(rulings["split:t1:1"].payload)) == (
        "split", {"amount": "2.00", "category": "food", "subcategory": "sub", "label": None, "date": None},
    )
    assert dict(rulings["split:t1:2"].payload) == {
        "amount": "3.00", "category": "misc", "subcategory": None, "label": "lunch", "date": "2026-01-06",
    }


def test_reimbursements_become_rulings(conn, tmp_path):
    run(_manual_repo(tmp_path, manual={"reimbursements": {"t1": "food"}}), conn)
    ruling = _by_id(conn, "rulings")["reimbursement:t1"]
    assert (ruling.kind, ruling.transaction_id, dict(ruling.payload)) == ("reimbursement", "t1", {"category": "food"})


def test_debts_and_people_imported(conn, tmp_path):
    manual = {
        "debt_people": {"alice": {"opening_balance": "10.00", "note": "start"}},
        "debts": {"t1": {"person": "alice", "kind": "lent", "note": "loan"}},
    }
    run(_manual_repo(tmp_path, manual=manual), conn)
    (person,) = db.load_inputs(conn, today=TODAY).people
    assert (person.id, person.display, person.opening_balance, person.note) == ("alice", "alice", Decimal("10.00"), "start")
    ruling = _by_id(conn, "rulings")["debt:t1"]
    assert (ruling.kind, dict(ruling.payload)) == ("debt", {"person": "alice", "kind": "lent", "note": "loan"})


_MANUAL_EXPENSES = {
    "manual-1": {"date": "2026-01-07", "account": "man1", "amount": "12.30", "category": "food",
                 "subcategory": "sub", "new_subcategory": True, "description": "paid cash", "note": "n1"},
    "manual-2": {"date": "2026-01-08", "account": "man1", "amount": "50.00", "category": "Income",
                 "description": "gift", "note": "n2", "direction": "income"},
}


def _manual_expense_repo(tmp_path):
    repo = _old_repo(
        tmp_path, accounts=(("acc1", "UAH", True), ("man1", "UAH", True)), manual={"manual_expenses": _MANUAL_EXPENSES},
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"))},
    )
    (repo / "data" / "categories.yaml").write_text(yaml.safe_dump({"categories": [{"name": "food"}, {"name": "Income"}]}))
    return repo


def test_manual_expenses_become_manual_transactions(conn, tmp_path):
    run(_manual_expense_repo(tmp_path), conn)
    inputs = db.load_inputs(conn, today=TODAY)
    manual = {t.id: t for t in inputs.transactions if t.source == "manual"}
    first, second = manual["manual-1"], manual["manual-2"]
    assert (first.account_id, first.date, first.time, first.amount, first.currency, first.direction) == (
        "man1", date(2026, 1, 7), datetime(2026, 1, 7, 12, 0), Decimal("12.30"), "UAH", "expense",
    )
    assert (first.mcc, first.description, first.comment) == (None, "paid cash", "n1")
    assert second.direction == "income" and {t.source for t in inputs.transactions if t.id == "t1"} == {"bank"}
    rulings = _by_id(conn, "rulings")
    assert (rulings["manual:manual-1"].kind, dict(rulings["manual:manual-1"].payload)) == (
        "category", {"category": "food", "subcategory": "sub", "note": "n1"},
    )
    assert rulings["manual:manual-2"].payload["subcategory"] is None


def test_manual_transactions_land_kept_in_rebuild_and_survive_reimport(conn, tmp_path):
    repo = _manual_expense_repo(tmp_path)
    run(repo, conn)
    run(repo, conn)
    kept = {e.transaction_id: (e.status, e.category, e.amount) for e in rebuild(conn, today=TODAY) if e.transaction_id.startswith("manual-")}
    assert kept == {"manual-1": ("kept", "food", Decimal("-12.30")), "manual-2": ("kept", "Income", Decimal("50.00"))}


def test_reclassify_rules_imported(conn, tmp_path, capsys):
    run(_manual_repo(tmp_path), conn)
    rules = {r.id: r for r in db.load_inputs(conn, today=TODAY).rules if r.kind != "categorize" and r.kind != "exclude"}
    assert sorted((r.kind, r.id, dict(r.match), dict(r.result)) for r in rules.values()) == [
        ("cancellation", "import-cancellation", {"description_prefix": "Скасування."}, {}),
        ("credit_repayment", "import-credit-repayment", {"description_prefix": "Погашення "}, {}),
        ("investment_withdrawal", "import-investment-withdrawal", {"description_contains": "інжур", "direction": "income"}, {}),
        ("person_return", "alice-return", {"description_equals": "From: alice"}, {"person": "alice", "category": "food"}),
        ("person_return", "bob-return", {"description_equals": "From: bob"}, {"person": "bob", "category": "misc"}),
    ]
    assert "imported reclassify rules: 5" in capsys.readouterr().out


def test_atm_cash_rule_is_imported(conn, tmp_path):
    rule = {"name": "atm-cash", "priority": 5, "match": {"description_contains": "atm"}, "category": "misc", "subcategory": "Cash"}
    run(_old_repo(tmp_path, rules=[rule], files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"))}), conn)
    stored = _by_id(conn, "rules")["atm-cash"]
    assert (stored.kind, dict(stored.match), dict(stored.result)) == (
        "categorize", {"description_contains": "atm"}, {"category": "misc", "subcategory": "Cash"},
    )


def test_missing_owners_key_raises_import_error(conn, tmp_path):
    repo = _manual_repo(tmp_path)
    (repo / "data" / "accounts.yaml").write_text(yaml.safe_dump({"budget_owner": "o", "accounts": []}))
    with pytest.raises(ValueError, match="owners: missing in accounts.yaml"):
        read_owners(repo / "data" / "accounts.yaml")


def test_unknown_cash_account_refuses(conn, tmp_path):
    repo = _old_repo(tmp_path, with_cash_account=False, files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1"))})
    with pytest.raises(ValueError, match="cash account 'cash-test' is not among the imported accounts"):
        run(repo, conn)


def test_imports_instalment_exception_ruling(conn, tmp_path):
    repo = _manual_repo(tmp_path, rows=("inst-1", "t2"))
    run(repo, conn)
    ruling = _by_id(conn, "rulings")["exclude:credit-drawdown:inst-1"]
    assert (ruling.kind, ruling.payload["reason"]) == ("exclude", "credit-drawdown")


def test_imports_exclude_rules_per_alternative(conn, tmp_path, capsys):
    run(_manual_repo(tmp_path), conn)
    rules = {r.id: r for r in db.load_inputs(conn, today=TODAY).rules if r.kind == "exclude"}
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
    assert not [r for r in db.load_inputs(conn, today=TODAY).rules if "розстрочка" in str(r.match.get("description_contains"))]


def test_optional_letter_alternative_expands_to_two_literals(conn, tmp_path):
    run(_manual_repo(tmp_path), conn)
    literals = {r.match["description_contains"] for r in db.load_inputs(conn, today=TODAY).rules if r.kind == "exclude"}
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
    entries = {e.transaction_id: e for e in rebuild(conn, today=TODAY)}
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
    stored = {(g.id, g.kind) for g in db.load_inputs(conn, today=TODAY).rulings}
    second = run(repo, conn)
    assert second == {t: (0, 0, 0, c.inserted) for t, c in first.items()}
    assert {(g.id, g.kind) for g in db.load_inputs(conn, today=TODAY).rulings} == stored


_INCOME = "2026-01-{day:02d},2026-01-{day:02d}T12:00:00+02:00,{amount},UAH,income,5411,salary,,,{tid}"


def _attribution_repo(tmp_path, attribution):
    return _old_repo(
        tmp_path,
        files={"acc1_2026-01-01_2026-01-31.csv": _csv(_row("t1", direction="income"), _row("t2"))},
        manual={"attribution": attribution},
    )


def test_attribution_becomes_attribute_rulings(conn, tmp_path):
    run(_attribution_repo(tmp_path, {"t1": "2026-01"}), conn)
    got = [r for r in db.load_inputs(conn, today=TODAY).rulings if r.kind == "attribute"]
    assert [(r.id, r.transaction_id, r.payload, r.created_by) for r in got] == [
        ("attribute:t1", "t1", {"budget_month": "2026-01"}, "import"),
    ]


def test_attribution_on_unknown_tx_refuses(conn, tmp_path):
    with pytest.raises(ValueError, match="attribution.*ghost"):
        run(_attribution_repo(tmp_path, {"t1": "2026-01", "ghost": "2026-02"}), conn)


def _fx_repo(tmp_path, *rows):
    return _old_repo(tmp_path, fx_rows=rows)


_RATE_A = "aaaaaaaaaaaa,,2026-01-07,41.0000,official,test-a,s1,,"
_RATE_B = "bbbbbbbbbbbb,,2026-01-07,41.2500,sell,test-b,,2026-01-07T10:00:00,note"


def test_fx_rates_imported_with_row_id(conn, tmp_path):
    run(_fx_repo(tmp_path, _RATE_A, _RATE_B), conn)
    got = sorted((x.id, x.date, x.rate, x.kind, x.method) for x in db.load_inputs(conn, today=TODAY).fx_rates)
    assert got == [
        ("aaaaaaaaaaaa", date(2026, 1, 7), Decimal("41.0000"), "official", "test-a"),
        ("bbbbbbbbbbbb", date(2026, 1, 7), Decimal("41.2500"), "sell", "test-b"),
    ]


def test_duplicate_fx_row_id_refuses(conn, tmp_path):
    with pytest.raises(ValueError, match="duplicate.*aaaaaaaaaaaa"):
        run(_fx_repo(tmp_path, _RATE_A, _RATE_A), conn)


def test_reimport_replaces_fx_rates(conn, tmp_path):
    run(_fx_repo(tmp_path, _RATE_A, _RATE_B), conn)
    other = tmp_path / "second"
    other.mkdir()
    run(_fx_repo(other, _RATE_B), conn)
    assert [x.id for x in db.load_inputs(conn, today=TODAY).fx_rates] == ["bbbbbbbbbbbb"]


def _accounts_after_import(conn, tmp_path):
    repo = _old_repo(tmp_path, accounts=(("acc1", "UAH", True), ("fop-test", "USD", True)))
    run(repo, conn)
    return {a.id: a for a in db.load_inputs(conn, today=TODAY).accounts}


def test_fop_account_gets_floor_step_100(conn, tmp_path):
    assert _accounts_after_import(conn, tmp_path)["fop-test"].fx_income_floor_step == Decimal("100")


def test_other_accounts_get_floor_step_5(conn, tmp_path):
    got = _accounts_after_import(conn, tmp_path)
    assert {i: a.fx_income_floor_step for i, a in got.items() if i != "fop-test"} == {"acc1": Decimal("5"), "cash-test": Decimal("5")}


def test_cash_opening_imported_on_cash_account(conn, tmp_path):
    got = _accounts_after_import(conn, tmp_path)
    assert got["cash-test"].cash_opening == Decimal("250.50")
    assert got["cash-test"].cash_from == date(2026, 11, 1)
    assert got["acc1"].cash_opening is None


def test_cli_reads_the_url_from_the_environment(conn, db_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MONEYFLOW_DATABASE_URL", db_url)
    main(["--old-repo", str(_db_repo(tmp_path))])
    assert "imported attribute rulings: 0" in capsys.readouterr().out
    assert len(db.load_inputs(conn, today=TODAY).rulings) == 2


def test_cli_without_db_and_without_the_variable_exits_with_the_message(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MONEYFLOW_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        main(["--old-repo", str(tmp_path)])
    assert "--db or MONEYFLOW_DATABASE_URL is required" in capsys.readouterr().err


_GRID = (
    ("2026-01", "food", None, "UAH", Decimal("100.00"), "template", "2026-01-02"),
    ("2026-01", "food", "cafe", "USD", Decimal("5.50"), None, None),
)


def _plan_repo(tmp_path: Path, rows=_GRID, token: str = "tok-test", pages: dict | None = None) -> Path:
    """A fake old repo whose `planexpand.read.read_grid` returns `rows` for a client built with `token`."""
    repo = _old_repo(tmp_path)
    python = repo / "scripts" / "python"
    for package in ("notionsync", "planexpand"):
        (python / package).mkdir()
        (python / package / "__init__.py").write_text("")
    (python / "notionsync" / "payloads.py").write_text(_PLAN_PAYLOADS_SOURCE)
    (python / "notionsync" / "client.py").write_text(_PLAN_CLIENT_SOURCE.format(pages=dict(pages or {})))
    (python / "notionsync" / "model.py").write_text(_NOTION_MODEL_SOURCE)
    (python / "planexpand" / "read.py").write_text(_PLAN_READ_SOURCE.format(rows=tuple(rows), token=token))
    return repo


def test_read_plan_maps_grid_rows_to_plan_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "tok-test")
    lines = read_plan(_plan_repo(tmp_path))
    assert [(p.id, p.budget_month, p.category, p.subcategory, p.currency, p.amount, p.source, p.handed_over)
            for p in lines] == [
        ("2026-01|food||UAH", "2026-01", "food", "", "UAH", Decimal("100.00"), "template", True),
        ("2026-01|food|cafe|USD", "2026-01", "food", "cafe", "USD", Decimal("5.50"), None, False),
    ]


def test_read_plan_reads_the_token_from_the_old_repo_env_file(tmp_path):
    repo = _plan_repo(tmp_path)
    (repo / ".env").write_text("NOTION_TOKEN=tok-test\n")
    assert len(read_plan(repo)) == 2


def test_read_plan_without_a_token_returns_none(tmp_path):
    assert read_plan(_plan_repo(tmp_path)) is None


def test_read_plan_refuses_a_duplicate_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "tok-test")
    with pytest.raises(ValueError, match="duplicate"):
        read_plan(_plan_repo(tmp_path, rows=(*_GRID, _GRID[0])))


def test_read_plan_refuses_a_row_without_a_month_and_never_prints_the_token(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "tok-test")
    rows = (*_GRID, (None, "food", None, "EUR", Decimal("1.00"), None, None))
    with pytest.raises(ValueError, match="1 row") as caught:
        read_plan(_plan_repo(tmp_path, rows=rows))
    assert "tok-test" not in str(caught.value)


def test_run_prints_the_skipped_line_without_a_token_and_leaves_plan_lines_alone(conn, tmp_path, capsys):
    counts = run(_plan_repo(tmp_path), conn)
    assert "plan_lines" not in counts
    assert capsys.readouterr().out.rstrip().endswith("plan_lines: skipped (no NOTION_TOKEN)")
    assert conn.execute("select count(*) as n from plan_lines").fetchone()["n"] == 0


def test_run_imports_the_plan_lines_and_a_second_run_changes_nothing(conn, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_TOKEN", "tok-test")
    repo = _plan_repo(tmp_path)
    assert run(repo, conn)["plan_lines"] == (2, 0, 0, 0)
    assert "plan_lines: 2 inserted, 0 updated, 0 deleted, 0 unchanged" in capsys.readouterr().out
    assert run(repo, conn)["plan_lines"] == (0, 0, 0, 2)
    assert "skipped" not in capsys.readouterr().out


def _month_page(page_id: str, title: str) -> dict:
    return {"id": page_id, "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}}}


def _mb_page(month_page_id, category, subcategory, currency, plan, *, rich_currency: bool = False) -> dict:
    """A Monthly Budget page in the Notion API shape the hybrid plan reader consumes."""
    if rich_currency:
        cur = {"type": "rich_text", "rich_text": [{"plain_text": currency}]}
    else:
        cur = {"type": "select", "select": {"name": currency}}
    return {
        "id": "mb-page",
        "properties": {
            "Month": {"type": "relation", "relation": [] if month_page_id is None else [{"id": month_page_id}]},
            "Category": {"type": "select", "select": {"name": category}},
            "Subcategory": {"type": "rich_text", "rich_text": [{"plain_text": subcategory}]},
            "Currency": cur,
            "Plan": {"type": "number", "number": plan},
        },
    }


_MONTHS = (_month_page("m-jan", "2026-01"), _month_page("m-feb", "2026-02"))
_HYBRID_GRID = (
    ("2026-01", "food", None, "UAH", Decimal("100.00"), "template", None),
    ("2026-01", "rent", None, "UAH", Decimal("900.00"), "template", None),
    ("2026-02", "food", None, "UAH", Decimal("110.00"), "template", None),
)


def _hybrid(tmp_path, monkeypatch, mb_pages, months=_MONTHS, rows=_HYBRID_GRID):
    monkeypatch.setenv("NOTION_TOKEN", "tok-test")
    pages = {"months": list(months), "monthly_budget": list(mb_pages)}
    return read_plan(_plan_repo(tmp_path, rows=rows, pages=pages))


def test_read_plan_takes_a_month_from_monthly_budget_and_a_later_month_from_the_grid(tmp_path, monkeypatch):
    lines = _hybrid(tmp_path, monkeypatch, [_mb_page("m-jan", "food", "", "UAH", 120)])
    assert [(p.id, p.amount, p.source, p.handed_over) for p in lines] == [
        ("2026-01|food||UAH", Decimal("120"), "monthly_budget", True),
        ("2026-02|food||UAH", Decimal("110.00"), "template", False),
    ]


def test_read_plan_drops_a_grid_key_of_a_monthly_budget_month_that_monthly_budget_lacks(tmp_path, monkeypatch):
    lines = _hybrid(tmp_path, monkeypatch, [_mb_page("m-jan", "food", "", "UAH", 120)])
    assert "2026-01|rent||UAH" not in [p.id for p in lines]


def test_read_plan_reads_the_no_subcategory_marker_as_empty_and_a_select_currency(tmp_path, monkeypatch):
    lines = _hybrid(tmp_path, monkeypatch, [_mb_page("m-jan", "tax", "<none>", "UAH", 120)])
    assert (lines[0].id, lines[0].subcategory, lines[0].currency) == ("2026-01|tax||UAH", "", "UAH")


def test_read_plan_reads_a_rich_text_currency_and_keeps_a_real_subcategory(tmp_path, monkeypatch):
    lines = _hybrid(tmp_path, monkeypatch, [_mb_page("m-jan", "food", "cafe", "USD", 5, rich_currency=True)])
    assert (lines[0].id, lines[0].subcategory, lines[0].currency) == ("2026-01|food|cafe|USD", "cafe", "USD")


def test_read_plan_skips_a_row_without_a_plan_and_keeps_a_plan_of_zero(tmp_path, monkeypatch):
    pages = [
        _mb_page("m-jan", "food", "", "UAH", None),
        _mb_page("m-jan", "rent", "", "UAH", 0),
    ]
    lines = _hybrid(tmp_path, monkeypatch, pages)
    assert [(p.id, p.amount) for p in lines if p.source == "monthly_budget"] == [("2026-01|rent||UAH", Decimal("0"))]


def test_read_plan_refuses_a_monthly_budget_row_with_no_month_without_naming_a_page(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="1 Monthly Budget row") as caught:
        _hybrid(tmp_path, monkeypatch, [_mb_page(None, "food", "", "UAH", 120)])
    assert "mb-page" not in str(caught.value) and "tok-test" not in str(caught.value)


def test_read_plan_refuses_a_monthly_budget_row_pointing_at_a_non_month_title(tmp_path, monkeypatch):
    months = (*_MONTHS, _month_page("m-odd", "Archive"))
    with pytest.raises(ValueError, match="1 Monthly Budget row") as caught:
        _hybrid(tmp_path, monkeypatch, [_mb_page("m-odd", "food", "", "UAH", 120)], months=months)
    assert "m-odd" not in str(caught.value)


def test_read_plan_refuses_two_monthly_budget_pages_on_one_key(tmp_path, monkeypatch):
    pages = [_mb_page("m-jan", "food", "", "UAH", 120), _mb_page("m-jan", "food", "", "UAH", 130)]
    with pytest.raises(ValueError, match=r"duplicate Monthly Budget key: 2026-01\|food\|\|UAH"):
        _hybrid(tmp_path, monkeypatch, pages)


def test_read_plan_without_a_token_returns_none_even_when_monthly_budget_has_pages(tmp_path):
    pages = {"months": list(_MONTHS), "monthly_budget": [_mb_page("m-jan", "food", "", "UAH", 120)]}
    assert read_plan(_plan_repo(tmp_path, pages=pages)) is None
