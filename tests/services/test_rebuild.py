from dataclasses import replace
from decimal import Decimal

import pytest

from moneyflow.__main__ import main
from moneyflow.core.model import BuildRefused, LedgerEntry
from moneyflow.services.rebuild import rebuild
from moneyflow.storage import db
from tests.core._builders import account, owner, rule, ruling, tx


def _seed(conn, rulings=()):
    db.replace_imported(
        conn,
        owners=(owner(),),
        accounts=(account(),),
        transactions=(tx("t1", description="cafe"), tx("t2", description="other")),
        rules=(replace(rule("r1", 10, 0, "food", None, description_contains="cafe"), created_by="import"),),
        rulings=rulings,
        categories=("food",), people=(),
    )


def test_rebuild_saves_ledger(conn):
    _seed(conn)
    ledger = rebuild(conn)
    assert db.load_ledger(conn) == ledger
    assert sorted((e.transaction_id, e.status) for e in ledger) == [("t1", "kept"), ("t2", "unresolved")]


def test_refusal_saves_nothing(conn):
    _seed(conn)
    prior = rebuild(conn)
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('bad', 't1', 'category', '{\"category\": \"nope\"}', 'operator')"
    )
    with pytest.raises(BuildRefused):
        rebuild(conn)
    assert db.load_ledger(conn) == prior
    assert prior


def test_cli_exit_1_on_refusal(conn, db_url, capsys):
    _seed(conn)
    prior = rebuild(conn)
    assert prior
    conn.execute(
        "insert into rulings (id, transaction_id, kind, payload, created_by)"
        " values ('bad', 't1', 'category', '{\"category\": \"nope\"}', 'operator')"
    )
    assert main(["rebuild", "--db", db_url]) == 1
    err = capsys.readouterr().err
    assert "unknown category nope" in err and "t1" in err
    assert db.load_ledger(conn) == prior


def test_cli_exit_1_on_broken_rule_value(conn, db_url, capsys):
    db.replace_imported(
        conn, owners=(owner(),), accounts=(account(),), transactions=(tx("t1"),),
        rules=(replace(rule("r1", 10, 0, "food", None, amount_lt="abc"), created_by="import"),),
        rulings=(), categories=("food",), people=(),
    )
    assert main(["rebuild", "--db", db_url]) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused: rule r1") and "Traceback" not in err


def test_cli_prints_counts(conn, db_url, capsys):
    _seed(conn)
    assert main(["rebuild", "--db", db_url]) == 0
    assert capsys.readouterr().out.strip() == "kept 1, unresolved 1, excluded 0"
