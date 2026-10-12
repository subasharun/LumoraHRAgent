"""Test the LLM planning loop without a real LLM.

A tiny fake OpenAI-compatible server returns scripted tool calls. This checks that, in LLM
mode, the orchestrator: sends the MCP-discovered tools to the model, executes the requested
calls through MCP, fills in the acting employee ID, strips `confirm=true` from ticket calls,
and removes citations to sections that do not exist.
"""

import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request

from hr_agent.agent.mcp_client import MCPToolClient
from hr_agent.agent.orchestrator import HRAgent
from hr_agent.config import get_settings

received: list[dict] = []


def _fake_llm_app() -> FastAPI:
    fake = FastAPI()

    @fake.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        received.append(body)
        has_tool_results = any(m.get("role") == "tool" for m in body["messages"])
        if not has_tool_results:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "lookup_employee_profile", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "check_policy_compliance", "arguments": json.dumps(
                    {"request_type": "work_from_abroad", "destination_country": "Portugal", "weeks": 6})}},
                {"id": "c3", "type": "function", "function": {"name": "create_mock_hr_ticket", "arguments": json.dumps(
                    {"category": "Global Mobility", "summary": "Review six weeks in Portugal", "confirm": True})}},
            ]}
        else:
            message = {"role": "assistant", "content": (
                "**Answer:** Not as requested: six weeks is about 30 workdays, above the 20-workday limit [POL-005 §4]. "
                "A made-up rule [POL-099 §1] should be removed.")}
        return {"id": "x", "object": "chat.completion", "created": int(time.time()), "model": "fake",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    return fake


@pytest.fixture(scope="module")
def fake_llm_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(_fake_llm_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True


async def test_llm_mode_calls_tools_through_mcp(fake_llm_url, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "llm")
    monkeypatch.setenv("LLM_BASE_URL", fake_llm_url)
    settings = get_settings()
    client = MCPToolClient(settings)
    await client.start()
    try:
        agent = HRAgent(client, settings)
        assert agent.mode == "llm"
        r = await agent.chat("Can I work from Portugal for six weeks?", employee_id="E1003")
    finally:
        await client.stop()

    # The model received the tool schemas discovered over MCP.
    sent_tools = {t["function"]["name"] for t in received[0]["tools"]}
    assert "check_policy_compliance" in sent_tools
    # The requested tools were executed via MCP with the acting employee filled in.
    calls = [s for s in r.trace if s["kind"] == "tool_call"]
    profile = next(s for s in calls if s["name"] == "lookup_employee_profile")
    assert profile["arguments"]["employee_id"] == "E1003"
    # Action safety: confirm=true was stripped, so only a preview exists.
    assert any(s["name"] == "confirmation_required" and s["status"] == "blocked" for s in r.trace)
    assert r.pending_action is not None
    # Citation verification kept the real section and removed the invented one.
    assert "POL-099" not in r.answer
    assert [c["citation"] for c in r.citations] == ["POL-005 §4"]
    assert r.mode == "llm"
