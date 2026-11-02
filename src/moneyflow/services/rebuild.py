"""Rebuild service: load inputs, build the ledger, save it. A refusal saves nothing."""

import psycopg

from moneyflow.core.build import build
from moneyflow.core.model import Ledger
from moneyflow.storage import db


def rebuild(conn: psycopg.Connection) -> Ledger:
    ledger = build(db.load_inputs(conn))
    db.save_ledger(conn, ledger)
    return ledger
