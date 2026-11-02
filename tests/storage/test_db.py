from dataclasses import replace
from decimal import Decimal

import psycopg
import pytest

from moneyflow.core.model import LedgerEntry
from moneyflow.storage import db
from tests.core._builders import account, rule, ruling, tx


def _entry(tid: str, category: str, amount: str = "-10.00") -> LedgerEntry:
    t = tx(tid)
    return LedgerEntry(tid, 0, t.date, "acc1", category, None, Decimal(amount), "UAH", "kept", None, "rule:r1")


def _write(conn, rules=(), rulings=(), transactions=(), categories=("food",)):
    return db.replace_imported(
        conn,
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
    assert got.accounts == (account(),)
    assert got.transactions == txs
    assert got.transactions[0].amount == Decimal("10.50")
    assert got.rules == rules
    assert got.rules[0].match == {"description_contains": "shop", "amount_lt": "500"}
    assert got.rules[0].result == {"category": "food", "subcategory": "cafe"}
    assert got.rulings == rulings
    assert got.categories == frozenset({"food", "misc"})
    assert counts == {"accounts": 1, "transactions": 2, "rules": 1, "rulings": 1, "categories": 2}


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
                db.replace_imported(other, accounts=(account(),), transactions=(tx("t1"), tx("t2")),
                                    rules=(), rulings=(), categories=("food",))
        return got

    monkeypatch.setattr(db, "_rows", rows_then_concurrent_insert)
    assert [t.id for t in db.load_inputs(conn).transactions] == ["t1"]
