"""Rebuild service: load inputs, build the ledger and its month summaries, save both. A refusal saves nothing."""

from datetime import date

import psycopg

from moneyflow.core.build import build
from moneyflow.core.cycles import build_cycles
from moneyflow.core.model import Ledger
from moneyflow.core.reports import month_summaries
from moneyflow.storage import db


def rebuild(conn: psycopg.Connection, *, today: date) -> Ledger:
    """`today` is required: the caller owns the clock (the CLI passes `date.today()`)."""
    inputs = db.load_inputs(conn, today=today)
    ledger = build(inputs)
    summaries = month_summaries(ledger, build_cycles(list(ledger), inputs), inputs)
    db.save_ledger(conn, ledger, summaries=summaries, built_for=today)
    return ledger
