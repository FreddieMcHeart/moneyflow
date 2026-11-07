"""Postgres storage: connection, migration, Inputs/Ledger load and save."""

from collections.abc import Iterable
from datetime import UTC, date
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from moneyflow.core.model import Account, FxRate, Inputs, Ledger, LedgerEntry, Owner, Person, Rule, Ruling, Transaction
from moneyflow.core.reports import MonthSummary

_TABLES = ("owners", "accounts", "transactions", "rules", "rulings", "categories", "people")
_TABLE_ORDER = ("owners", "accounts", "transactions", "rules", "rulings", "categories", "people", "fx_rates")
_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def connect(url: str) -> psycopg.Connection:
    """Autocommit connection in UTC, rows as dicts; `conn.transaction()` scopes each write."""
    return psycopg.connect(url, autocommit=True, row_factory=dict_row, options="-c timezone=UTC")


def migrate(url: str) -> None:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def _naive_utc(value):
    return value.astimezone(UTC).replace(tzinfo=None)


def _owner(r: dict) -> Owner:
    return Owner(r["id"], r["display"], tuple(r["counterparty_strings"]), r["budget"])


def _account(r: dict) -> Account:
    return Account(
        r["id"], r["name"], r["currency"], r["owner"], r["role"], r["include"], r["cash_from"],
        r["fx_income_floor_step"], r["cash_opening"],
    )


def _fx_rate(r: dict) -> FxRate:
    return FxRate(r["id"], r["date"], r["rate"], r["kind"], r["method"])


def _transaction(r: dict) -> Transaction:
    return Transaction(
        id=r["id"], account_id=r["account_id"], date=r["date"], time=_naive_utc(r["time"]),
        amount=r["amount"], currency=r["currency"], direction=r["direction"], mcc=r["mcc"],
        description=r["description"], comment=r["comment"],
        source=r["source"],
    )


def _person(r: dict) -> Person:
    return Person(r["id"], r["display"], r["opening_balance"], r["note"])


def _rule(r: dict) -> Rule:
    return Rule(r["id"], r["kind"], r["priority"], r["ordinal"], r["match"], r["result"], r["active"], r["created_by"])


def _ruling(r: dict) -> Ruling:
    return Ruling(r["id"], r["transaction_id"], r["kind"], r["payload"], r["created_by"])


def _entry(r: dict) -> LedgerEntry:
    return LedgerEntry(
        r["transaction_id"], r["part"], r["date"], r["account_id"], r["category"], r["subcategory"],
        r["amount"], r["currency"], r["status"], r["reason"], r["decided_by"], r["direction"], r["via"],
        r["budget_month"], r["amount_uah"], r["rate"], r["rate_kind"],
    )


def _rows(conn: psycopg.Connection, query: str) -> list[dict]:
    return conn.execute(query).fetchall()  # type: ignore[return-value]


def load_inputs(conn: psycopg.Connection, *, today: date) -> Inputs:
    """Everything the core needs, read from one snapshot; only active rules. `today` comes from the caller."""
    with conn.transaction():
        conn.execute("set transaction isolation level repeatable read")
        return Inputs(
            owners=tuple(_owner(r) for r in _rows(conn, "select * from owners order by id")),
            accounts=tuple(_account(r) for r in _rows(conn, "select * from accounts order by id")),
            transactions=tuple(_transaction(r) for r in _rows(conn, "select * from transactions order by id")),
            rules=tuple(
                _rule(r) for r in _rows(conn, "select * from rules where active order by priority, ordinal, id")
            ),
            rulings=tuple(_ruling(r) for r in _rows(conn, "select * from rulings order by id")),
            categories=frozenset(r["name"] for r in _rows(conn, "select name from categories")),
            people=tuple(_person(r) for r in _rows(conn, "select * from people order by id")),
            fx_rates=tuple(_fx_rate(r) for r in _rows(conn, "select * from fx_rates order by id")),
            today=today,
        )


def stale_transaction_ids(conn: psycopg.Connection, source_ids: Iterable[str]) -> list[str]:
    """Stored transaction ids not in `source_ids`; reported by the import, never deleted."""
    rows = conn.execute(
        "select id from transactions where not (id = any(%s)) order by id", (list(source_ids),)
    ).fetchall()
    return [r["id"] for r in rows]  # type: ignore[index]


def save_ledger(
    conn: psycopg.Connection, ledger: Ledger, *, summaries: Iterable[MonthSummary], built_for: date
) -> None:
    """Replace the stored ledger and its month summaries in one transaction.

    `summaries` come from `core.reports.month_summaries`; one row is written per (budget month, currency) that
    has income or actual spending, and `built_for` is the `today` they were computed for. Nothing is summed here.
    """
    rows = [
        (e.transaction_id, e.part, e.date, e.account_id, e.category, e.subcategory, e.amount,
         e.currency, e.status, e.reason, e.decided_by, e.direction, e.via,
         e.budget_month, e.amount_uah, e.rate, e.rate_kind)
        for e in ledger
    ]
    summary_rows = [
        (s.budget_month, currency, s.income.get(currency, Decimal(0)), s.total_actual.get(currency, Decimal(0)),
         s.cycle_start, s.cycle_end, s.cycle_open, s.days_total, s.days_left, s.cash_in_hand, built_for)
        for s in summaries
        for currency in sorted(set(s.income) | set(s.total_actual))
    ]
    with conn.transaction():
        conn.execute("delete from ledger_entries")
        conn.execute("delete from month_summaries")
        with conn.cursor() as cur:
            cur.executemany(
                "insert into ledger_entries (transaction_id, part, date, account_id, category, subcategory,"
                " amount, currency, status, reason, decided_by, direction, via,"
                " budget_month, amount_uah, rate, rate_kind)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )
            cur.executemany(
                "insert into month_summaries (budget_month, currency, income, total_actual, cycle_start,"
                " cycle_end, cycle_open, days_total, days_left, cash_in_hand, built_for)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                summary_rows,
            )


def load_ledger(conn: psycopg.Connection) -> Ledger:
    return tuple(_entry(r) for r in _rows(conn, "select * from ledger_entries order by transaction_id, part"))


class Counts(NamedTuple):
    """What one import did to one table; `unchanged` is the incoming rows that needed no write."""

    inserted: int
    updated: int
    deleted: int
    unchanged: int


def _insert_many(conn: psycopg.Connection, sql: str, rows: Iterable[tuple]) -> None:
    with conn.cursor() as cur:
        cur.executemany(sql, list(rows))


def _upsert_changed(
    conn: psycopg.Connection, table: str, columns: tuple[str, ...], rows: list[tuple]
) -> tuple[set[str], set[str]]:
    """Insert new rows by id, update only rows whose stored columns differ; return (inserted ids, updated ids).

    `xmax = 0` in `returning` marks an insert; a row that is unchanged is not written and not returned.
    """
    rest = columns[1:]
    sql = (
        f"insert into {table} ({', '.join(columns)}) values ({', '.join(['%s'] * len(columns))})"
        f" on conflict ({columns[0]}) do update set {', '.join(f'{c}=excluded.{c}' for c in rest)}"
        f" where ({', '.join(f'{table}.{c}' for c in rest)}) is distinct from ({', '.join(f'excluded.{c}' for c in rest)})"
        f" returning {columns[0]} as id, (xmax = 0) as inserted"
    )
    inserted: set[str] = set()
    updated: set[str] = set()
    if not rows:
        return inserted, updated
    with conn.cursor() as cur:
        cur.executemany(sql, rows, returning=True)  # type: ignore[arg-type]
        while True:
            for r in cur.fetchall():
                (inserted if r["inserted"] else updated).add(r["id"])  # type: ignore[index]
            if not cur.nextset():
                break
    return inserted, updated


def _upsert_facts(
    conn: psycopg.Connection,
    owners: Iterable[Owner],
    accounts: Iterable[Account],
    transactions: Iterable[Transaction],
) -> dict[str, Counts]:
    owners = tuple(owners)
    # Demote first so a changed budget owner never trips the one-budget-owner index mid-upsert; only
    # owners that lose the flag are written.
    keep = [o.id for o in owners if o.budget]
    demoted = {
        r["id"]
        for r in conn.execute(
            "update owners set budget = false where budget and id <> all(%s) returning id", (keep,)
        ).fetchall()
    }
    owner_rows = [(o.id, o.display, list(o.counterparty_strings), o.budget) for o in owners]
    account_rows = [
        (a.id, a.name, a.currency, a.owner, a.role, a.include, a.cash_from, a.fx_income_floor_step, a.cash_opening)
        for a in accounts
    ]
    tx_rows = [
        (t.id, t.account_id, t.date, t.time, t.amount, t.currency, t.direction, t.mcc, t.description, t.comment,
         t.source)
        for t in transactions
    ]
    out: dict[str, Counts] = {}
    specs = (
        ("owners", ("id", "display", "counterparty_strings", "budget"), owner_rows),
        ("accounts", ("id", "name", "currency", "owner", "role", "include", "cash_from", "fx_income_floor_step",
                      "cash_opening"), account_rows),
        ("transactions", ("id", "account_id", "date", "time", "amount", "currency", "direction", "mcc",
                          "description", "comment", "source"), tx_rows),
    )
    for table, columns, rows in specs:
        inserted, updated = _upsert_changed(conn, table, columns, rows)
        if table == "owners":
            updated |= demoted - inserted
        out[table] = Counts(len(inserted), len(updated), 0, len(rows) - len(inserted) - len(updated))
    return out


class ImportCollision(Exception):
    """An incoming import ruling reuses the id of a ruling the operator created."""

    def __init__(self, ruling_id: str) -> None:
        super().__init__(f"import ruling {ruling_id!r} collides with an operator ruling of the same id")
        self.ruling_id = ruling_id


def _refuse_operator_collision(conn: psycopg.Connection, rulings: tuple[Ruling, ...]) -> None:
    held = {
        r["id"]
        for r in conn.execute(
            "select id from rulings where created_by <> 'import' and id = any(%s)",
            ([g.id for g in rulings],),
        ).fetchall()
    }
    for g in rulings:
        if g.id in held:
            raise ImportCollision(g.id)


def _sync_owned(
    conn: psycopg.Connection,
    table: str,
    columns: tuple[str, ...],
    incoming: dict[str, tuple],
    *,
    scope: str = "true",
    scope_params: tuple = (),
    constant: tuple[str, str] | None = None,
    jsonb: frozenset[str] = frozenset(),
) -> Counts:
    """Make the rows in `scope` equal `incoming` by id: delete missing, update changed, insert new.

    Only rows inside `scope` are ever read, updated or deleted, so rows of other owners are untouched.
    `constant` is a (column, sql literal) written on insert and never compared (`created_by`).
    """
    key = columns[0]
    rest = columns[1:]
    existing = {
        r[key]: tuple(r[c] for c in columns)
        for r in conn.execute(
            f"select {', '.join(columns)} from {table} where {scope}", scope_params  # type: ignore[arg-type]
        ).fetchall()
    }
    gone = [k for k in existing if k not in incoming]
    new = [v for k, v in incoming.items() if k not in existing]
    changed = [v for k, v in incoming.items() if k in existing and existing[k] != v]

    def wrap(values: tuple) -> tuple:
        return tuple(Jsonb(v) if c in jsonb else v for c, v in zip(columns, values, strict=True))

    if gone:
        conn.execute(f"delete from {table} where {key} = any(%s) and {scope}", (gone, *scope_params))  # type: ignore[arg-type]
    if changed and rest:
        _insert_many(
            conn,
            f"update {table} set {', '.join(f'{c}=%s' for c in rest)} where {key}=%s and {scope}",  # type: ignore[arg-type]
            ((*wrap(v)[1:], v[0], *scope_params) for v in changed),
        )
    if new:
        cols = columns + ((constant[0],) if constant else ())
        marks = ", ".join(["%s"] * len(columns)) + (f", {constant[1]}" if constant else "")
        _insert_many(
            conn, f"insert into {table} ({', '.join(cols)}) values ({marks})", (wrap(v) for v in new)  # type: ignore[arg-type]
        )
    return Counts(len(new), len(changed), len(gone), len(incoming) - len(new) - len(changed))


def _replace_decisions(
    conn: psycopg.Connection,
    rules: Iterable[Rule],
    rulings: Iterable[Ruling],
    categories: Iterable[str],
    people: Iterable[Person],
    transaction_ids: list[str],
) -> dict[str, Counts]:
    rulings = tuple(rulings)
    _refuse_operator_collision(conn, rulings)
    # Import-owned rows only are compared: an operator or agent row is never updated or deleted. An import
    # ruling on a transaction missing from the source (stale) is outside the scope and kept: it is still
    # that transaction's decision. People have no operator rows yet and are synced whole.
    owned = "created_by = 'import'"
    by = ("created_by", "'import'")
    return {
        "categories": _sync_owned(conn, "categories", ("name",), {c: (c,) for c in categories}, scope=owned, constant=by),
        "people": _sync_owned(
            conn, "people", ("id", "display", "opening_balance", "note"),
            {p.id: (p.id, p.display, p.opening_balance, p.note) for p in people},
        ),
        "rules": _sync_owned(
            conn, "rules", ("id", "kind", "priority", "ordinal", "match", "result", "active"),
            {r.id: (r.id, r.kind, r.priority, r.ordinal, dict(r.match), dict(r.result), r.active) for r in rules},
            scope=owned, constant=by, jsonb=frozenset({"match", "result"}),
        ),
        "rulings": _sync_owned(
            conn, "rulings", ("id", "transaction_id", "kind", "payload"),
            {g.id: (g.id, g.transaction_id, g.kind, dict(g.payload)) for g in rulings},
            scope=f"{owned} and transaction_id = any(%s)", scope_params=(transaction_ids,),
            constant=by, jsonb=frozenset({"payload"}),
        ),
    }


def _replace_fx_rates(conn: psycopg.Connection, fx_rates: Iterable[FxRate]) -> Counts:
    return _sync_owned(
        conn, "fx_rates", ("id", "date", "rate", "kind", "method"),
        {x.id: (x.id, x.date, x.rate, x.kind, x.method) for x in fx_rates},
    )


def replace_imported(
    conn: psycopg.Connection,
    *,
    owners: Iterable[Owner],
    accounts: Iterable[Account],
    transactions: Iterable[Transaction],
    rules: Iterable[Rule],
    rulings: Iterable[Ruling],
    categories: Iterable[str],
    people: Iterable[Person],
    fx_rates: Iterable[FxRate],
) -> dict[str, Counts]:
    """One transaction: write only what changed, return (inserted, updated, deleted, unchanged) per table.

    Facts are upserted by id with a change test; import-owned decision rows, people and FX rates are
    compared by id against what is stored. A re-import of unchanged input writes nothing. Stored
    transactions missing from the source are never deleted.

    An import ruling whose transaction is not among the incoming `transactions` survives the
    re-import. Such a ruling can name a category that a later `categories.yaml` drops; `categorize`
    then refuses `unknown category` (0 occurrences today).
    """
    transactions = tuple(transactions)
    with conn.transaction():
        counts = _upsert_facts(conn, owners, accounts, transactions)
        counts |= _replace_decisions(conn, rules, rulings, categories, people, [t.id for t in transactions])
        counts["fx_rates"] = _replace_fx_rates(conn, fx_rates)
        return {t: counts[t] for t in _TABLE_ORDER}
