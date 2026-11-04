"""Rebuild service: load inputs, build the ledger, save it. A refusal saves nothing."""

from datetime import date

import psycopg

from moneyflow.core.build import build
from moneyflow.core.model import Ledger
from moneyflow.storage import db


def rebuild(conn: psycopg.Connection) -> Ledger:
    ledger = build(db.load_inputs(conn, today=date.today()))
    db.save_ledger(conn, ledger)
    return ledger
