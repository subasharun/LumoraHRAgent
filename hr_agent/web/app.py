"""FastAPI web application: chat UI, /chat API, /health, and demo helpers.

Start locally:
    uvicorn hr_agent.web.app:app --reload --port 8000

On startup the app launches the MCP server (stdio) as a child process — or connects to
MCP_SERVER_URL when MCP_TRANSPORT=http — and discovers its tools. All agent work goes
through that MCP connection.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agent.mcp_client import MCPToolClient
from ..agent.orchestrator import HRAgent
from ..config import ROOT_DIR, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hr_agent.web")

STATIC_DIR = Path(__file__).parent / "static"
STARTED_AT = time.time()

# The two required demo tasks. The UI shows them as buttons; graders can also POST them to /chat.
DEMO_TASKS = [
    {
        "id": "remote-work-abroad",
        "title": "Remote work eligibility (work from abroad)",
        "employee_id": "E1003",
        "message": "Can I work remotely from Portugal for six weeks this summer?",
        "expected_tools": ["search_policy_documents", "lookup_employee_profile", "search_policy_documents",
                           "check_policy_compliance", "create_mock_hr_ticket (preview, needs confirmation)"],
        "expected_outcome": "Not compliant: ~30 workdays exceeds the 20-workday WFA limit (POL-005 §4); options to shorten, "
                            "combine with PTO, or request a Global Mobility review; ticket preview awaiting confirmation.",
    },
    {
        "id": "pto-request",
        "title": "PTO request guidance",
        "employee_id": "E1002",
        "message": "Can I take 3 days of PTO next week? Please draft a note to my manager.",
        "expected_tools": ["search_policy_documents", "lookup_employee_profile", "check_pto_balance",
                           "search_policy_documents", "check_policy_compliance", "draft_hr_email"],
        "expected_outcome": "Balance is sufficient (7 days after pending); notice rule for 2–4 days is 5 business days "
                            "(POL-002 §5); manager approval required; draft email to the manager (not sent).",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    client = MCPToolClient(settings)
    try:
        await client.start()
    except Exception as exc:  # the app still starts; /health reports the problem and the agent degrades
        log.error("MCP server failed to start: %s", exc)
    app.state.client = client
    app.state.agent = HRAgent(client, settings)
    log.info("Agent mode: %s", app.state.agent.mode)
    yield
    await client.stop()


app = FastAPI(title="Lumora HR Agent", version="1.0.0", lifespan=lifespan,
              description="Agentic RAG HR assistant using MCP tools (course project; synthetic data).")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str = Field("", max_length=2000, description="The user's question or request.")
    employee_id: str | None = Field(None, description="Acting employee (simulated login), e.g. E1003.")
    session_id: str | None = Field(None, description="Keeps pending actions and short history between turns.")
    confirm_action_id: str | None = Field(None, description="Confirm a pending mock action (e.g. ticket creation).")
    cancel_action_id: str | None = Field(None, description="Cancel a pending mock action.")
    mode: str | None = Field(None, pattern="^(llm|rules)$", description="Force the planner (default: auto).")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/chat")
async def chat(req: ChatRequest) -> JSONResponse:
    """Run the agent. Returns the answer, citations (with snippets), the tool-call trace and any pending action."""
    if not req.message.strip() and not (req.confirm_action_id or req.cancel_action_id):
        raise HTTPException(status_code=422, detail="message is required")
    result = await app.state.agent.chat(
        req.message, employee_id=req.employee_id, session_id=req.session_id,
        confirm_action_id=req.confirm_action_id, cancel_action_id=req.cancel_action_id, mode=req.mode,
    )
    return JSONResponse(result.to_dict())


@app.get("/health")
async def health() -> JSONResponse:
    """App status plus MCP connectivity (ping + discovered tools)."""
    client: MCPToolClient = app.state.client
    agent: HRAgent = app.state.agent
    mcp_status = await client.health()
    settings = get_settings()
    body = {
        "status": "ok" if mcp_status.get("connected") else "degraded",
        "app": "lumora-hr-agent",
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "agent_mode": agent.mode,
        "llm": {"provider_url": settings.llm_base_url, "model": settings.llm_model,
                "configured": bool(settings.llm_api_key), "error": agent.llm_error},
        "mcp": mcp_status,
    }
    return JSONResponse(body, status_code=200)


@app.get("/api/tools")
async def tools() -> dict:
    """Tools discovered from the MCP server via tools/list (names, descriptions, JSON schemas)."""
    client: MCPToolClient = app.state.client
    return {"tools": [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                      for t in client.tools.values()]}


@app.get("/api/demo-tasks")
async def demo_tasks() -> dict:
    return {"tasks": DEMO_TASKS}


@app.get("/api/employees")
async def employees() -> dict:
    """Synthetic employees for the 'Acting as' picker (a stand-in for a real login)."""
    raw = json.loads((ROOT_DIR / "mock_data" / "employees.json").read_text())
    return {"employees": [{"employee_id": e["employee_id"], "name": e["name"], "title": e["title"],
                           "employment_type": e["employment_type"]} for e in raw["employees"]]}
