"""MCP client used by the agent orchestrator.

How it works:
- On startup the client launches the MCP server as a child process (stdio transport)
  or connects to a separately deployed server (Streamable HTTP), runs the MCP
  `initialize` handshake, and calls `tools/list` to *discover* the tools and their
  JSON schemas. Nothing about the tools is hard-coded on the agent side.
- One background task owns the connection for the whole life of the web app, so
  each chat request reuses the open session instead of starting a new process.
- If the server dies or a call times out, the next call restarts the connection
  once and retries. Failures are returned as `{"error": ...}` dicts so the agent can
  degrade gracefully (e.g. tell the user a tool is unavailable) instead of crashing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ..config import ROOT_DIR, Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """A tool as discovered from the MCP server."""

    name: str
    description: str
    input_schema: dict

    def to_openai(self) -> dict:
        """Convert to the OpenAI/Groq function-calling format, with a compacted schema.

        Free-tier LLM APIs limit tokens per minute, so we drop schema noise that does not help
        the model ("title" fields, "default": null, and `anyOf: [X, null]` wrappers for optional
        fields). This roughly halves the tokens spent on tool definitions in every request.
        """
        return {
            "type": "function",
            "function": {"name": self.name, "description": " ".join(self.description.split()),
                         "parameters": _compact_schema(self.input_schema)},
        }


def _compact_schema(node):
    if isinstance(node, list):
        return [_compact_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    node = {k: v for k, v in node.items() if k != "title" and not (k == "default" and v is None)}
    any_of = node.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        inner = next(x for x in any_of if x != {"type": "null"})
        node = {**{k: v for k, v in node.items() if k != "anyOf"}, **inner}
    return {k: _compact_schema(v) for k, v in node.items()}


@dataclass
class ToolCallResult:
    name: str
    arguments: dict
    output: dict
    ok: bool
    duration_ms: float
    error: str | None = None


@dataclass
class MCPToolClient:
    settings: Settings = field(default_factory=get_settings)
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    server_name: str = ""
    _session: ClientSession | None = None
    _owner_task: asyncio.Task | None = None
    _ready: asyncio.Event | None = None
    _stop: asyncio.Event | None = None
    _lock: asyncio.Lock | None = None
    _start_error: str | None = None
    connected_at: float | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Open the MCP connection and discover tools. Safe to call more than once."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.is_connected:
                return
            self._ready, self._stop = asyncio.Event(), asyncio.Event()
            self._start_error = None
            self._owner_task = asyncio.create_task(self._own_connection(), name="mcp-connection")
            # Wait until the session is ready or the connection attempt failed.
            waiter = asyncio.create_task(self._ready.wait())
            done, _ = await asyncio.wait({waiter, self._owner_task}, timeout=90, return_when=asyncio.FIRST_COMPLETED)
            if waiter not in done:
                waiter.cancel()
                raise RuntimeError(self._start_error or "MCP server did not become ready within 90s")

    async def _own_connection(self) -> None:
        """Background task: keep the transport and session open until stop() is called.

        Entering and leaving the transport context in the same task is required by the
        MCP SDK (it uses anyio task groups), which is why this lives in its own task.
        """
        try:
            async with self._transport() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    self.server_name = getattr(getattr(init, "server_info", None), "name", "") or "lumora-hr"
                    listed = await session.list_tools()
                    self.tools = {
                        t.name: ToolSpec(t.name, t.description or "", t.input_schema)
                        for t in listed.tools
                        if t.name not in self.settings.disabled_tools
                    }
                    self._session = session
                    self.connected_at = time.time()
                    log.info("MCP connected (%s): %d tools discovered", self.settings.mcp_transport, len(self.tools))
                    self._ready.set()
                    await self._stop.wait()
        except Exception as exc:  # pragma: no cover - exercised by failure tests
            self._start_error = f"{type(exc).__name__}: {exc}"
            log.error("MCP connection ended: %s", self._start_error)
        finally:
            self._session = None

    def _transport(self):
        if self.settings.mcp_transport == "http":
            from mcp.client.streamable_http import streamable_http_client

            return streamable_http_client(self.settings.mcp_server_url)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=str(ROOT_DIR),
            env={**os.environ, "PYTHONPATH": str(ROOT_DIR)},
        )
        return stdio_client(params)

    async def stop(self) -> None:
        if self._stop:
            self._stop.set()
        if self._owner_task:
            try:
                await asyncio.wait_for(self._owner_task, timeout=10)
            except (asyncio.TimeoutError, Exception):
                self._owner_task.cancel()
        self._session = None

    @property
    def is_connected(self) -> bool:
        return self._session is not None and self._owner_task is not None and not self._owner_task.done()

    # ------------------------------------------------------------------ calls
    def openai_tools(self) -> list[dict]:
        return [t.to_openai() for t in self.tools.values()]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolCallResult:
        """Call one MCP tool. Never raises: errors come back in the result."""
        started = time.perf_counter()
        if name not in self.tools:
            # Either the tool does not exist or it was disabled (ablation / failure simulation).
            return ToolCallResult(name, arguments, {"error": "tool_unavailable", "message": f"Tool '{name}' is not available."},
                                  False, 0.0, "tool_unavailable")
        for attempt in (1, 2):
            try:
                if not self.is_connected:
                    await self.start()
                result = await asyncio.wait_for(
                    self._session.call_tool(name, arguments), timeout=self.settings.mcp_call_timeout_s
                )
                output = _parse_result(result)
                ok = not getattr(result, "is_error", False) and "error" not in output
                return ToolCallResult(name, arguments, output, ok, _ms(started), None if ok else output.get("error", "tool_error"))
            except Exception as exc:
                log.warning("MCP call %s failed (attempt %d): %s", name, attempt, exc)
                if attempt == 1:
                    await self.stop()  # force a reconnect on the retry
                    continue
                return ToolCallResult(name, arguments, {"error": "mcp_unavailable", "message": str(exc) or type(exc).__name__},
                                      False, _ms(started), "mcp_unavailable")
        raise AssertionError("unreachable")

    async def health(self) -> dict:
        status = {"transport": self.settings.mcp_transport, "connected": self.is_connected,
                  "server": self.server_name, "tools": sorted(self.tools)}
        if self.is_connected:
            try:
                started = time.perf_counter()
                await asyncio.wait_for(self._session.send_ping(), timeout=5)
                status["ping_ms"] = _ms(started)
            except Exception as exc:
                status.update(connected=False, error=str(exc))
        elif self._start_error:
            status["error"] = self._start_error
        return status


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _parse_result(result: Any) -> dict:
    """Tools return JSON objects; the SDK delivers them as structured content or JSON text."""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    texts = [c.text for c in getattr(result, "content", []) if getattr(c, "type", "") == "text"]
    raw = "\n".join(texts)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        if getattr(result, "is_error", False):
            return {"error": "tool_error", "message": raw}
        return {"text": raw}
