"""Read-only MCP server `moneyflow` over stdio. Tools format only; numbers come from the services."""

from datetime import date

from mcp.server.fastmcp import FastMCP

from moneyflow.services import queries
from moneyflow.storage import db

def _money(amount) -> str:
    return str(amount)


def _tool_spending(db_url: str, category: str, date_from: str, date_to: str) -> dict[str, str]:
    with db.connect(db_url) as conn:
        result = queries.spending(conn, category, date.fromisoformat(date_from), date.fromisoformat(date_to))
    return {currency: _money(amount) for currency, amount in result.items()}


def _iso(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _tool_category_totals(
    db_url: str, date_from: str | None, date_to: str | None, budget_month: str | None
) -> list[dict]:
    with db.connect(db_url) as conn:
        rows = queries.category_totals(conn, _iso(date_from), _iso(date_to), budget_month=budget_month)
    return [{"category": c, "subcategory": s, "currency": cur, "amount": _money(a)} for c, s, cur, a in rows]


def _tool_month_summary(db_url: str, budget_month: str | None) -> dict[str, object]:
    with db.connect(db_url) as conn:
        m = queries.month_summary(conn, budget_month, today=date.today())
    return {
        "budget_month": m.budget_month, "cycle_start": m.cycle_start.isoformat(),
        "cycle_end": None if m.cycle_end is None else m.cycle_end.isoformat(), "cycle_open": m.cycle_open,
        "days_total": m.days_total, "days_left": m.days_left,
        "income": {c: _money(a) for c, a in m.income.items()},
        "total_actual": {c: _money(a) for c, a in m.total_actual.items()},
        "cash_in_hand": None if m.cash_in_hand is None else _money(m.cash_in_hand),
    }


def _tool_find_transactions(
    db_url: str, date_from: str, date_to: str, text: str | None, account_id: str | None, limit: int
) -> list[dict]:
    with db.connect(db_url) as conn:
        rows = queries.find_transactions(
            conn, text=text, date_from=date.fromisoformat(date_from), date_to=date.fromisoformat(date_to),
            account_id=account_id, limit=limit,
        )
    return [
        {k: (v.isoformat() if isinstance(v, date) else _money(v) if k.endswith("amount") else v) for k, v in r.items()}
        for r in rows
    ]


def create_server(db_url: str) -> FastMCP:
    def spending(category: str, date_from: str, date_to: str) -> dict[str, str]:
        """Signed kept amount per currency for one category over [date_from, date_to], both inclusive."""
        return _tool_spending(db_url, category, date_from, date_to)

    def category_totals(
        date_from: str | None = None, date_to: str | None = None, budget_month: str | None = None
    ) -> list[dict]:
        """Signed kept amount per (category, subcategory, currency), either over [date_from, date_to] (both
        inclusive) or over one budget month (YYYY-MM), not both."""
        return _tool_category_totals(db_url, date_from, date_to, budget_month)

    def month_summary(budget_month: str | None = None) -> dict[str, object]:
        """One budget cycle: dates, days total and left, income and total actual per currency (original-currency
        amounts, never folded into one number) and cash in hand (null before the cash cutover). budget_month
        (YYYY-MM) defaults to the open cycle."""
        return _tool_month_summary(db_url, budget_month)

    def find_transactions(
        date_from: str, date_to: str, text: str | None = None, account_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Transactions with their ledger entry. `text` is a case-insensitive substring of description or comment."""
        return _tool_find_transactions(db_url, date_from, date_to, text, account_id, limit)

    server = FastMCP("moneyflow")
    for tool in (spending, category_totals, find_transactions, month_summary):
        server.tool()(tool)
    return server
