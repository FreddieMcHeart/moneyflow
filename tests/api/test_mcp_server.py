import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from moneyflow.api.mcp_server import create_server
from tests.services.test_queries import _seed, _seed_cycle


def _call(db_url, name, args):
    async def run():
        async with create_connected_server_and_client_session(create_server(db_url)._mcp_server) as client:
            return await client.call_tool(name, args)

    return anyio.run(run)


def _payload(result):
    assert not result.isError, result.content
    data = result.structuredContent
    return data["result"] if list(data) == ["result"] else data


def test_mcp_lists_four_tools(db_url):
    async def run():
        async with create_connected_server_and_client_session(create_server(db_url)._mcp_server) as client:
            return await client.list_tools()

    tools = {t.name: t for t in anyio.run(run).tools}
    assert set(tools) == {"spending", "category_totals", "find_transactions", "month_summary"}


def test_mcp_no_not_ported_warning(db_url):
    async def run():
        async with create_connected_server_and_client_session(create_server(db_url)._mcp_server) as client:
            return await client.list_tools()

    for tool in anyio.run(run).tools:
        assert "WRONG" not in (tool.description or ""), tool.name
        assert "Phase 0 does not port" not in (tool.description or ""), tool.name


def test_mcp_spending_returns_strings(conn, db_url):
    _seed(conn)
    out = _payload(_call(db_url, "spending", {"category": "Cafe", "date_from": "2026-01-01", "date_to": "2026-01-31"}))
    assert out == {"UAH": "-14.75", "USD": "-1.00"}


def test_mcp_other_tools_return_strings(conn, db_url):
    _seed(conn)
    rows = _payload(_call(db_url, "category_totals", {"date_from": "2026-01-01", "date_to": "2026-01-31"}))
    assert {"category": "Transport", "subcategory": None, "currency": "UAH", "amount": "-3.00"} in rows
    found = _payload(_call(db_url, "find_transactions",
                           {"text": "latte", "date_from": "2026-01-01", "date_to": "2026-01-31", "limit": 5}))
    assert [f["id"] for f in found] == ["t1"] and found[0]["amount"] == "-10.50" and found[0]["date"] == "2026-01-02"


def test_mcp_month_summary_open_cycle(conn, db_url):
    _seed_cycle(conn)
    out = _payload(_call(db_url, "month_summary", {}))
    assert out["budget_month"] == "2026-01" and out["cycle_open"] is True
    assert out["cycle_start"] == "2026-01-01" and out["cycle_end"] is None
    assert out["income"] == {"UAH": "1000.00"} and out["total_actual"] == {"UAH": "-40.00"}
    assert out["cash_in_hand"] is None
    named = _payload(_call(db_url, "month_summary", {"budget_month": "2026-01"}))
    assert named == out


def test_mcp_category_totals_by_budget_month(conn, db_url):
    _seed_cycle(conn)
    rows = _payload(_call(db_url, "category_totals", {"budget_month": "2026-01"}))
    assert {"category": "Cafe", "subcategory": None, "currency": "UAH", "amount": "-40.00"} in rows
