"""Read-only MCP server `moneyflow` over stdio. Tools format only; numbers come from the services."""

from datetime import date

from mcp.server.fastmcp import FastMCP

from moneyflow.services import queries
from moneyflow.storage import db

_NOT_PORTED = """
    WARNING, phase 0: these numbers are WRONG for any budget cycle, and must not be read as "preliminary".
    Phase 0 does not port these old pipeline stages: exclude, split, refund, apply_reimbursements,
    apply_debts, apply_person_returns, apply_investment_withdrawals, and cycle assignment.
    Totals and income over a cycle therefore differ from the old ledger (measured 2026-11-01: cycle 2026-10
    differs by 264,649.00 UAH). Amounts are signed (income +, expense -) strings; dates are ISO (YYYY-MM-DD)."""


def _money(amount) -> str:
    return str(amount)


def _tool_spending(db_url: str, category: str, date_from: str, date_to: str) -> dict[str, str]:
    with db.connect(db_url) as conn:
        result = queries.spending(conn, category, date.fromisoformat(date_from), date.fromisoformat(date_to))
    return {currency: _money(amount) for currency, amount in result.items()}


def _tool_category_totals(db_url: str, date_from: str, date_to: str) -> list[dict]:
    with db.connect(db_url) as conn:
        rows = queries.category_totals(conn, date.fromisoformat(date_from), date.fromisoformat(date_to))
    return [{"category": c, "subcategory": s, "currency": cur, "amount": _money(a)} for c, s, cur, a in rows]


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

    def category_totals(date_from: str, date_to: str) -> list[dict]:
        """Signed kept amount per (category, subcategory, currency) over [date_from, date_to], both inclusive."""
        return _tool_category_totals(db_url, date_from, date_to)

    def find_transactions(
        date_from: str, date_to: str, text: str | None = None, account_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Transactions with their ledger entry. `text` is a case-insensitive substring of description or comment."""
        return _tool_find_transactions(db_url, date_from, date_to, text, account_id, limit)

    spending.__doc__ += _NOT_PORTED  # type: ignore[operator]
    category_totals.__doc__ += _NOT_PORTED  # type: ignore[operator]
    server = FastMCP("moneyflow")
    for tool in (spending, category_totals, find_transactions):
        server.tool()(tool)
    return server
