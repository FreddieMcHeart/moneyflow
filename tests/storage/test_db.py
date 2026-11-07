from dataclasses import replace
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from moneyflow.core.model import FxRate, LedgerEntry, Ruling
from moneyflow.storage import db
from tests.core._builders import TODAY, account, owner, person, rule, ruling, tx


def _entry(tid: str, category: str, amount: str = "-10.00") -> LedgerEntry:
    t = tx(tid)
    return LedgerEntry(tid, 0, t.date, "acc1", category, None, Decimal(amount), "UAH", "kept", None, "rule:r1", "expense", None, None, None, None, None)


def _write(conn, rules=(), rulings=(), transactions=(), categories=("food",), people=(), accounts=None, fx_rates=()):
    return db.replace_imported(
        conn,
        owners=(owner(),),
        accounts=accounts or (account(),),
        transactions=transactions,
        rules=rules,
        rulings=rulings,
        categories=categories,
        people=people,
        fx_rates=fx_rates,
    )


def _import_rule(id: str, **match):
    return replace(rule(id, 10, 0, "food", "cafe", **match), created_by="import")


def test_round_trip_inputs(conn):
    txs = (tx("t1", amount="10.50"), tx("t2", amount="0.01", direction="income"))
    rules = (_import_rule("r1", description_contains="shop", amount_lt="500"),)
    rulings = (replace(ruling("g1", "t1", "food", None), created_by="import"),)
    counts = _write(conn, rules, rulings, txs, ("food", "misc"))
    got = db.load_inputs(conn, today=TODAY)
    assert got.owners == (owner(),)
    assert got.accounts == (account(),)
    assert got.transactions == txs
    assert got.transactions[0].amount == Decimal("10.50")
    assert got.rules == rules
    assert got.rules[0].match == {"description_contains": "shop", "amount_lt": "500"}
    assert got.rules[0].result == {"category": "food", "subcategory": "cafe"}
    assert got.rulings == rulings
    assert got.categories == frozenset({"food", "misc"})
    assert {t: c.inserted for t, c in counts.items()} == {
        "owners": 1, "accounts": 1, "transactions": 2, "rules": 1, "rulings": 1, "categories": 2, "people": 0,
        "fx_rates": 0}


def test_inactive_rules_are_not_loaded(conn):
    _write(conn, rules=(replace(_import_rule("r1"), active=False), _import_rule("r2")))
    assert [r.id for r in db.load_inputs(conn, today=TODAY).rules] == ["r2"]


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
    assert [r.id for r in db.load_inputs(conn, today=TODAY).rulings] == ["op1"]


def test_replace_imported_keeps_operator_rules_and_categories(conn):
    _write(conn, rules=(_import_rule("r1"),), categories=("food",))
    conn.execute(
        "insert into rules (id, kind, priority, ordinal, match, result, active, created_by)"
        " select 'oprule', kind, priority, ordinal, match, result, active, 'operator' from rules where id = 'r1'"
    )
    conn.execute("insert into categories (name, created_by) values ('opcat', 'operator')")
    _write(conn, rules=(_import_rule("r2"),), categories=("misc",))
    got = db.load_inputs(conn, today=TODAY)
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
                                    rules=(), rulings=(), categories=("food",), people=(), fx_rates=())
        return got

    monkeypatch.setattr(db, "_rows", rows_then_concurrent_insert)
    assert [t.id for t in db.load_inputs(conn, today=TODAY).transactions] == ["t1"]


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
    got = db.load_inputs(conn, today=TODAY).rulings
    assert len(got) == 1
    assert got[0].payload["category"] == "misc"


def _replace(conn, owners, accounts, rules=(), rulings=(), transactions=()):
    return db.replace_imported(
        conn, owners=owners, accounts=accounts, transactions=transactions,
        rules=rules, rulings=rulings, categories=("food",), people=(), fx_rates=())


def test_owners_round_trip(conn):
    owners = (owner("a", budget=True, counterparty_strings=("ann", "a b")), owner("b", budget=False))
    _replace(conn, owners, (replace(account(), owner="a"),))
    got = db.load_inputs(conn, today=TODAY).owners
    assert got == owners
    assert isinstance(got[0].counterparty_strings, tuple)


def test_second_budget_owner_is_rejected_by_db(conn):
    owners = (owner("a", budget=True), owner("b", budget=True))
    with pytest.raises(psycopg.errors.UniqueViolation):
        _replace(conn, owners, (replace(account(), owner="a"),))


def test_cash_from_round_trip(conn):
    cash = replace(account("cash1"), cash_from=date(2026, 11, 1))
    _replace(conn, (owner(),), (account(), cash))
    got = {a.id: a.cash_from for a in db.load_inputs(conn, today=TODAY).accounts}
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
    got = db.load_inputs(conn, today=TODAY)
    assert got.rules == (exclude_rule,)
    assert got.rules[0].result == {"reason": "miles-to-charity", "paired": True}
    assert got.rulings == (exclude_ruling, white)
    assert got.rulings[1].payload == {}


def test_account_with_unknown_owner_is_rejected_by_db(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _replace(conn, (owner("a"),), (replace(account(), owner="ghost"),))


def test_reimport_with_ruling_id_held_by_operator_raises_import_collision(conn):
    t1 = tx("t1")
    _write(conn, transactions=(t1,))
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('override:t1', 't1', 'category', '{\"category\": \"misc\", \"subcategory\": null}', 'operator')"
    )
    with pytest.raises(db.ImportCollision) as err:
        _write(conn, transactions=(t1,), rulings=(ruling("override:t1", "t1", "food"),))
    assert err.value.ruling_id == "override:t1"
    rows = conn.execute("select id, created_by from rulings").fetchall()
    assert [(r["id"], r["created_by"]) for r in rows] == [("override:t1", "operator")]


def test_unknown_rule_kind_rejected_by_db(conn):
    bad = replace(rule("r1", 10, 0, "food", None, description_contains="x"), kind="bogus", created_by="import")
    with pytest.raises(psycopg.errors.CheckViolation):
        _write(conn, rules=(bad,))


def test_new_rule_and_ruling_kinds_accepted_by_db(conn):
    rules = tuple(
        replace(rule(f"r{i}", 10, i, "food", None, description_contains="x"), kind=k, created_by="import")
        for i, k in enumerate(("credit_repayment", "cancellation", "person_return", "investment_withdrawal"))
    )
    rulings = tuple(Ruling(f"{k}:t1", "t1", k, {}, "import") for k in ("split", "reimbursement", "debt"))
    _write(conn, rules=rules, rulings=rulings, transactions=(tx("t1"),))
    assert [r.kind for r in db.load_inputs(conn, today=TODAY).rules] == [r.kind for r in rules]


def test_people_round_trip(conn):
    people = (person("alice", "Alice", "12.50", "a note"), person("bob", "Bob", "0.00"))
    counts = _write(conn, people=people)
    assert db.load_inputs(conn, today=TODAY).people == people
    assert counts["people"] == (2, 0, 0, 0)


def test_transaction_source_round_trip(conn):
    txs = (tx("t1"), tx("m1", source="manual"))
    _write(conn, transactions=txs)
    assert {t.id: t.source for t in db.load_inputs(conn, today=TODAY).transactions} == {"t1": "bank", "m1": "manual"}


def test_reimport_replaces_manual_transaction(conn):
    first = tx("manual-1", amount="10.00", source="manual")
    second = replace(first, amount=Decimal("25.00"), description="edited")
    _write(conn, transactions=(first,), rulings=(ruling("manual:manual-1", "manual-1", "food"),))
    _write(conn, transactions=(second,), rulings=(ruling("manual:manual-1", "manual-1", "misc"),),
           categories=("food", "misc"))
    got = db.load_inputs(conn, today=TODAY)
    assert [(t.id, t.source, t.amount, t.description) for t in got.transactions] == [
        ("manual-1", "manual", Decimal("25.00"), "edited")
    ]
    assert [(g.id, g.payload["category"]) for g in got.rulings] == [("manual:manual-1", "misc")]


def test_fx_rates_round_trip(conn):
    rates = (
        FxRate("r1", date(2026, 1, 5), Decimal("41.2500"), "sell", "bank"),
        FxRate("r2", date(2026, 1, 5), Decimal("41.1000"), "official", "nbu"),
        FxRate("r3", date(2026, 1, 6), Decimal("41.3333"), "effective", "paired-transfer"),
        FxRate("r4", date(2026, 1, 7), Decimal("41.4000"), "midpoint", "bank-currency"),
    )
    _write(conn, fx_rates=rates)
    assert db.load_inputs(conn, today=TODAY).fx_rates == rates


def test_account_floor_step_and_cash_opening_round_trip(conn):
    accounts = (
        replace(account("fop", "USD"), fx_income_floor_step=Decimal("100")),
        replace(account("cash"), cash_from=date(2026, 11, 1), cash_opening=Decimal("1234.50")),
        account("plain"),
    )
    _write(conn, accounts=accounts)
    got = {a.id: a for a in db.load_inputs(conn, today=TODAY).accounts}
    assert got["fop"].fx_income_floor_step == Decimal("100")
    assert got["cash"].cash_opening == Decimal("1234.50")
    assert got["plain"].fx_income_floor_step == Decimal("5")
    assert got["plain"].cash_opening is None


def test_attribute_ruling_kind_accepted_by_db(conn):
    attribute = Ruling("attribute:t1", "t1", "attribute", {"budget_month": "2026-01"}, "import")
    _write(conn, rulings=(attribute,), transactions=(tx("t1"),))
    assert db.load_inputs(conn, today=TODAY).rulings == (attribute,)


def test_outside_cycle_status_accepted_by_db(conn):
    entry = replace(_entry("t1", "food"), status="outside_cycle")
    db.save_ledger(conn, (entry,))
    assert db.load_ledger(conn) == (entry,)


def test_ledger_entry_fx_columns_round_trip(conn):
    entry = replace(
        _entry("t1", "food"), budget_month="2026-01", amount_uah=Decimal("-415.00"),
        rate=Decimal("41.5000"), rate_kind="sell",
    )
    bare = _entry("t2", "food")
    db.save_ledger(conn, (entry, bare))
    assert db.load_ledger(conn) == (entry, bare)
    assert (bare.budget_month, bare.amount_uah, bare.rate, bare.rate_kind) == (None, None, None, None)


def test_save_ledger_writes_cycle_and_fx_columns(conn):
    usd = replace(
        _entry("t1", "food"), currency="USD", budget_month="2026-01", amount_uah=Decimal("-415.00"),
        rate=Decimal("41.5000"), rate_kind="sell",
    )
    outside = replace(_entry("t2", "food"), status="outside_cycle")
    db.save_ledger(conn, (usd, outside))
    rows = conn.execute(
        "select transaction_id, status, budget_month, amount_uah, rate, rate_kind"
        " from ledger_entries order by transaction_id"
    ).fetchall()
    assert rows == [
        {"transaction_id": "t1", "status": "kept", "budget_month": "2026-01", "amount_uah": Decimal("-415.00"),
         "rate": Decimal("41.5000"), "rate_kind": "sell"},
        {"transaction_id": "t2", "status": "outside_cycle", "budget_month": None, "amount_uah": None,
         "rate": None, "rate_kind": None},
    ]


def test_load_inputs_carries_today(conn):
    _write(conn)
    assert db.load_inputs(conn, today=TODAY).today == TODAY
