"""MCP tests: the server starts over stdio, tools are discoverable, and tool calls work.

These go through the real MCP protocol (initialize → tools/list → tools/call), exactly
like the agent does.
"""

import pytest

from hr_agent.agent.mcp_client import MCPToolClient
from hr_agent.config import get_settings

REQUIRED_TOOLS = {
    "search_policy_documents", "get_policy_section", "lookup_employee_profile", "check_pto_balance",
    "lookup_benefits_status", "check_policy_compliance", "create_mock_hr_ticket", "draft_hr_email",
}


@pytest.fixture
async def client():
    c = MCPToolClient(get_settings())
    await c.start()
    yield c
    await c.stop()


async def test_tool_discovery(client):
    assert REQUIRED_TOOLS <= set(client.tools)
    schema = client.tools["check_policy_compliance"].input_schema
    assert schema["type"] == "object"
    assert "request_type" in schema["properties"] and "employee_id" in schema["required"]


async def test_rag_tool_call(client):
    r = await client.call("search_policy_documents", {"query": "notice for PTO", "k": 3})
    assert r.ok
    assert r.output["results"][0]["doc_id"] == "POL-002"
    assert all({"citation", "snippet", "title", "section"} <= set(hit) for hit in r.output["results"])


async def test_mock_data_tool_call(client):
    r = await client.call("check_pto_balance", {"employee_id": "E1002"})
    assert r.ok and r.output["available_after_pending_days"] == 7.0


async def test_ticket_requires_confirmation(client):
    preview = await client.call("create_mock_hr_ticket", {"employee_id": "E1003", "category": "Global Mobility", "summary": "test"})
    assert preview.output["status"] == "preview" and preview.output["confirmation_required"]
    created = await client.call("create_mock_hr_ticket", {"employee_id": "E1003", "category": "Global Mobility",
                                                          "summary": "test", "confirm": True})
    assert created.output["status"] == "created" and created.output["ticket"]["ticket_id"].startswith("MOCK-HR-")


async def test_errors_are_graceful(client):
    missing = await client.call("lookup_employee_profile", {"employee_id": "E9999"})
    assert not missing.ok and missing.output["error"] == "employee_not_found"
    unknown = await client.call("delete_everything", {})
    assert not unknown.ok and unknown.error == "tool_unavailable"
