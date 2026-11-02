"""Read queries over the stored ledger. Every number comes from `core.reports`; nothing is summed here."""

from datetime import date
from decimal import Decimal

import psycopg

from moneyflow.core.reports import category_totals as _core_totals
from moneyflow.storage import db


def _totals(conn: psycopg.Connection, date_from: date, date_to: date) -> dict[tuple[str, str | None, str], Decimal]:
    return _core_totals(db.load_ledger(conn), date_from, date_to)


def spending(conn: psycopg.Connection, category: str, date_from: date, date_to: date) -> dict[str, Decimal]:
    """Signed kept amount per currency for one category, summed over its subcategories."""
    per_currency: dict[str, list[Decimal]] = {}
    for (cat, _sub, currency), amount in _totals(conn, date_from, date_to).items():
        if cat == category:
            per_currency.setdefault(currency, []).append(amount)
    return {currency: sum(amounts, Decimal(0)) for currency, amounts in per_currency.items()}


def category_totals(
    conn: psycopg.Connection, date_from: date, date_to: date
) -> list[tuple[str, str | None, str, Decimal]]:
    """(category, subcategory, currency, amount) rows, ordered by category, subcategory (none first), currency."""
    rows = [(c, s, cur, amount) for (c, s, cur), amount in _totals(conn, date_from, date_to).items()]
    return sorted(rows, key=lambda r: (r[0], r[1] is not None, r[1] or "", r[2]))


_FIND = """
select t.id, t.date, t.account_id, t.description, t.comment, t.amount as fact_amount, t.currency as fact_currency,
       e.category, e.subcategory, e.amount, e.currency, e.status
from transactions t left join ledger_entries e on e.transaction_id = t.id
where t.date between %(date_from)s and %(date_to)s
  and (%(account_id)s::text is null or t.account_id = %(account_id)s)
  and (%(text)s::text is null
       or strpos(lower(t.description || ' ' || t.comment), lower(%(text)s)) > 0)
order by t.date desc, t.id, e.part
limit %(limit)s
"""


def find_transactions(
    conn: psycopg.Connection, *, text: str | None, date_from: date, date_to: date,
    account_id: str | None, limit: int,
) -> list[dict]:
    """Facts joined to ledger entries; `text` is a case-insensitive substring of description and comment."""
    params = {"text": text, "date_from": date_from, "date_to": date_to, "account_id": account_id, "limit": limit}
    return conn.execute(_FIND, params).fetchall()  # type: ignore[arg-type,return-value]
