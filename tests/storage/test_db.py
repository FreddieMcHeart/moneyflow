from dataclasses import replace
from decimal import Decimal

import psycopg
import pytest

from moneyflow.core.model import LedgerEntry
from moneyflow.storage import db
from datetime import date

from moneyflow.core.model import Ruling
from tests.core._builders import account, owner, rule, ruling, tx


def _entry(tid: str, category: str, amount: str = "-10.00") -> LedgerEntry:
    t = tx(tid)
    return LedgerEntry(tid, 0, t.date, "acc1", category, None, Decimal(amount), "UAH", "kept", None, "rule:r1")


def _write(conn, rules=(), rulings=(), transactions=(), categories=("food",)):
    return db.replace_imported(
        conn,
        owners=(owner(),),
        accounts=(account(),),
        transactions=transactions,
        rules=rules,
        rulings=rulings,
        categories=categories,
    )


def _import_rule(id: str, **match):
    return replace(rule(id, 10, 0, "food", "cafe", **match), created_by="import")


def test_round_trip_inputs(conn):
    txs = (tx("t1", amount="10.50"), tx("t2", amount="0.01", direction="income"))
    rules = (_import_rule("r1", description_contains="shop", amount_lt="500"),)
    rulings = (replace(ruling("g1", "t1", "food", None), created_by="import"),)
    counts = _write(conn, rules, rulings, txs, ("food", "misc"))
    got = db.load_inputs(conn)
    assert got.owners == (owner(),)
    assert got.accounts == (account(),)
    assert got.transactions == txs
    assert got.transactions[0].amount == Decimal("10.50")
    assert got.rules == rules
    assert got.rules[0].match == {"description_contains": "shop", "amount_lt": "500"}
    assert got.rules[0].result == {"category": "food", "subcategory": "cafe"}
    assert got.rulings == rulings
    assert got.categories == frozenset({"food", "misc"})
    assert counts == {"owners": 1, "accounts": 1, "transactions": 2, "rules": 1, "rulings": 1, "categories": 2}


def test_inactive_rules_are_not_loaded(conn):
    _write(conn, rules=(replace(_import_rule("r1"), active=False), _import_rule("r2")))
    assert [r.id for r in db.load_inputs(conn).rules] == ["r2"]


def test_save_ledger_replaces(conn):
    db.save_ledger(conn, (_entry("t1", "food"), _entry("t2", "misc")))
    second = (_entry("t3", "misc", "-5.25"),)
    db.save_ledger(conn, second)
    assert db.load_ledger(conn) == second


def test_replace_imported_keeps_operator_rows(conn):
    txs = (tx("t1"),)
    _write(conn, transactions=txs)
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('op1', 't1', 'category', '{\"category\": \"misc\", \"subcategory\": null}', 'operator')"
    )
    _write(conn, transactions=txs)
    assert [r.id for r in db.load_inputs(conn).rulings] == ["op1"]


def test_replace_imported_keeps_operator_rules_and_categories(conn):
    _write(conn, rules=(_import_rule("r1"),), categories=("food",))
    conn.execute(
        "insert into rules (id, kind, priority, ordinal, match, result, active, created_by)"
        " select 'oprule', kind, priority, ordinal, match, result, active, 'operator' from rules where id = 'r1'"
    )
    conn.execute("insert into categories (name, created_by) values ('opcat', 'operator')")
    _write(conn, rules=(_import_rule("r2"),), categories=("misc",))
    got = db.load_inputs(conn)
    assert {r.id for r in got.rules} == {"oprule", "r2"}
    assert got.categories == frozenset({"opcat", "misc"})


def test_negative_amount_rejected_by_db(conn):
    bad = replace(tx("t1"), amount=Decimal("-1.00"))
    with pytest.raises(psycopg.errors.CheckViolation):
        _write(conn, transactions=(bad,))


def test_load_inputs_reads_one_snapshot(conn, db_url, monkeypatch):
    _write(conn, transactions=(tx("t1"),))
    real_rows = db._rows
    calls = []

    def rows_then_concurrent_insert(c, query):
        got = real_rows(c, query)
        if not calls:
            calls.append(query)  # before the insert: replace_imported below calls _rows too
            with db.connect(db_url) as other:
                db.replace_imported(other, owners=(owner(),), accounts=(account(),), transactions=(tx("t1"), tx("t2")),
                                    rules=(), rulings=(), categories=("food",))
        return got

    monkeypatch.setattr(db, "_rows", rows_then_concurrent_insert)
    assert [t.id for t in db.load_inputs(conn).transactions] == ["t1"]


def test_reimport_keeps_ruling_of_transaction_missing_from_source(conn):
    t1, t2 = tx("t1"), tx("t2")
    _write(conn, transactions=(t1, t2), rulings=(ruling("override:t1", "t1", "food"),))
    _write(conn, transactions=(t2,))
    rows = conn.execute("select id, created_by from rulings order by id").fetchall()
    assert [(r["id"], r["created_by"]) for r in rows] == [("override:t1", "import")]
    assert db.stale_transaction_ids(conn, ["t2"]) == ["t1"]


def test_reimport_replaces_ruling_of_transaction_still_in_source(conn):
    # negative control: passes before the fix, proved by mutation (see the task report)
    t1 = tx("t1")
    _write(conn, transactions=(t1,), rulings=(ruling("override:t1", "t1", "food"),))
    _write(conn, transactions=(t1,), rulings=(ruling("override:t1", "t1", "misc"),), categories=("food", "misc"))
    got = db.load_inputs(conn).rulings
    assert len(got) == 1
    assert got[0].payload["category"] == "misc"


def _replace(conn, owners, accounts, rules=(), rulings=(), transactions=()):
    return db.replace_imported(
        conn, owners=owners, accounts=accounts, transactions=transactions,
        rules=rules, rulings=rulings, categories=("food",),
    )


def test_owners_round_trip(conn):
    owners = (owner("a", budget=True, counterparty_strings=("ann", "a b")), owner("b", budget=False))
    _replace(conn, owners, (replace(account(), owner="a"),))
    got = db.load_inputs(conn).owners
    assert got == owners
    assert isinstance(got[0].counterparty_strings, tuple)


def test_second_budget_owner_is_rejected_by_db(conn):
    owners = (owner("a", budget=True), owner("b", budget=True))
    with pytest.raises(psycopg.errors.UniqueViolation):
        _replace(conn, owners, (replace(account(), owner="a"),))


def test_cash_from_round_trip(conn):
    cash = replace(account("cash1"), cash_from=date(2026, 11, 1))
    _replace(conn, (owner(),), (account(), cash))
    got = {a.id: a.cash_from for a in db.load_inputs(conn).accounts}
    assert got == {"acc1": None, "cash1": date(2026, 11, 1)}


def test_exclude_rule_and_ruling_kinds_round_trip(conn):
    exclude_rule = replace(
        rule("x1", 10, 0, "food", description_contains="kb"), kind="exclude", created_by="import",
        result={"reason": "miles-to-charity", "paired": True},
    )
    exclude_ruling = Ruling("exclude:r:t1", "t1", "exclude", {"reason": "r", "note": None}, "import")
    white = Ruling("white_card:t1", "t1", "white_card", {}, "import")
    _replace(conn, (owner(),), (account(),), rules=(exclude_rule,), rulings=(exclude_ruling, white),
             transactions=(tx("t1"),))
    got = db.load_inputs(conn)
    assert got.rules == (exclude_rule,)
    assert got.rules[0].result == {"reason": "miles-to-charity", "paired": True}
    assert got.rulings == (exclude_ruling, white)
    assert got.rulings[1].payload == {}


def test_account_with_unknown_owner_is_rejected_by_db(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _replace(conn, (owner("a"),), (replace(account(), owner="ghost"),))
