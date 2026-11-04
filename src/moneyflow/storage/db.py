"""Postgres storage: connection, migration, Inputs/Ledger load and save."""

from collections.abc import Iterable
from datetime import UTC, date
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from moneyflow.core.model import Account, FxRate, Inputs, Ledger, LedgerEntry, Owner, Person, Rule, Ruling, Transaction

_TABLES = ("owners", "accounts", "transactions", "rules", "rulings", "categories", "people")
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


def save_ledger(conn: psycopg.Connection, ledger: Ledger) -> None:
    """Replace the stored ledger in one transaction."""
    rows = [
        (e.transaction_id, e.part, e.date, e.account_id, e.category, e.subcategory, e.amount,
         e.currency, e.status, e.reason, e.decided_by, e.direction, e.via,
         e.budget_month, e.amount_uah, e.rate, e.rate_kind)
        for e in ledger
    ]
    with conn.transaction():
        conn.execute("delete from ledger_entries")
        with conn.cursor() as cur:
            cur.executemany(
                "insert into ledger_entries (transaction_id, part, date, account_id, category, subcategory,"
                " amount, currency, status, reason, decided_by, direction, via,"
                " budget_month, amount_uah, rate, rate_kind)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )


def load_ledger(conn: psycopg.Connection) -> Ledger:
    return tuple(_entry(r) for r in _rows(conn, "select * from ledger_entries order by transaction_id, part"))


def _insert_many(conn: psycopg.Connection, sql: str, rows: Iterable[tuple]) -> None:
    with conn.cursor() as cur:
        cur.executemany(sql, list(rows))


def _upsert_facts(
    conn: psycopg.Connection,
    owners: Iterable[Owner],
    accounts: Iterable[Account],
    transactions: Iterable[Transaction],
) -> None:
    owners = tuple(owners)
    # Demote first so a changed budget owner never trips the one-budget-owner index mid-upsert.
    conn.execute("update owners set budget = false")
    _insert_many(
        conn,
        "insert into owners (id, display, counterparty_strings, budget) values (%s,%s,%s,%s)"
        " on conflict (id) do update set display=excluded.display,"
        " counterparty_strings=excluded.counterparty_strings, budget=excluded.budget",
        ((o.id, o.display, list(o.counterparty_strings), o.budget) for o in owners),
    )
    _insert_many(
        conn,
        "insert into accounts (id, name, currency, owner, role, include, cash_from, fx_income_floor_step,"
        " cash_opening) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (id) do update set name=excluded.name, currency=excluded.currency,"
        " owner=excluded.owner, role=excluded.role, include=excluded.include, cash_from=excluded.cash_from,"
        " fx_income_floor_step=excluded.fx_income_floor_step, cash_opening=excluded.cash_opening",
        ((a.id, a.name, a.currency, a.owner, a.role, a.include, a.cash_from, a.fx_income_floor_step,
          a.cash_opening) for a in accounts),
    )
    _insert_many(
        conn,
        "insert into transactions (id, account_id, date, time, amount, currency, direction, mcc,"
        " description, comment, source) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (id) do update set account_id=excluded.account_id, date=excluded.date,"
        " time=excluded.time, amount=excluded.amount, currency=excluded.currency,"
        " direction=excluded.direction, mcc=excluded.mcc, description=excluded.description,"
        " comment=excluded.comment, source=excluded.source",
        ((t.id, t.account_id, t.date, t.time, t.amount, t.currency, t.direction, t.mcc,
          t.description, t.comment, t.source) for t in transactions),
    )


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


def _replace_decisions(
    conn: psycopg.Connection,
    rules: Iterable[Rule],
    rulings: Iterable[Ruling],
    categories: Iterable[str],
    people: Iterable[Person],
    transaction_ids: list[str],
) -> None:
    rulings = tuple(rulings)
    _refuse_operator_collision(conn, rulings)
    # An import ruling on a transaction missing from the source (stale) is kept: it is still that
    # transaction's decision. Rules, categories and people are replaced whole, as before.
    conn.execute(
        "delete from rulings where created_by = 'import' and transaction_id = any(%s)", (transaction_ids,)
    )
    for table in ("rules", "categories"):
        conn.execute(f"delete from {table} where created_by = 'import'")  # type: ignore[arg-type]
    conn.execute("delete from people")  # people have no operator rows yet; replaced whole
    _insert_many(
        conn,
        "insert into categories (name, created_by) values (%s, 'import')",
        ((c,) for c in categories),
    )
    _insert_many(
        conn,
        "insert into people (id, display, opening_balance, note) values (%s,%s,%s,%s)",
        ((p.id, p.display, p.opening_balance, p.note) for p in people),
    )
    _insert_many(
        conn,
        "insert into rules (id, kind, priority, ordinal, match, result, active, created_by)"
        " values (%s,%s,%s,%s,%s,%s,%s,'import')",
        ((r.id, r.kind, r.priority, r.ordinal, Jsonb(dict(r.match)), Jsonb(dict(r.result)), r.active)
         for r in rules),
    )
    _insert_many(
        conn,
        "insert into rulings (id, transaction_id, kind, payload, created_by) values (%s,%s,%s,%s,'import')",
        ((g.id, g.transaction_id, g.kind, Jsonb(dict(g.payload))) for g in rulings),
    )


def _replace_fx_rates(conn: psycopg.Connection, fx_rates: Iterable[FxRate]) -> None:
    conn.execute("delete from fx_rates")
    _insert_many(
        conn,
        "insert into fx_rates (id, date, rate, kind, method) values (%s,%s,%s,%s,%s)",
        ((x.id, x.date, x.rate, x.kind, x.method) for x in fx_rates),
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
) -> dict[str, int]:
    """One transaction: upsert facts by id, replace the `import` decision rows, return counts per table.

    FX rates are replaced whole: the table holds only imported observations.

    An import ruling whose transaction is not among the incoming `transactions` survives the
    re-import. Such a ruling can name a category that a later `categories.yaml` drops; `categorize`
    then refuses `unknown category` (0 occurrences today).
    """
    transactions = tuple(transactions)
    with conn.transaction():
        _upsert_facts(conn, owners, accounts, transactions)
        _replace_decisions(conn, rules, rulings, categories, people, [t.id for t in transactions])
        _replace_fx_rates(conn, fx_rates)
        return {t: _rows(conn, f"select count(*) as n from {t}")[0]["n"] for t in _TABLES}  # type: ignore[arg-type]
