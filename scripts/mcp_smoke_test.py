"""Standalone MCP smoke test: start the MCP server over stdio, list its tools, call two of them.

Used in CI and handy for graders:
    python scripts/mcp_smoke_test.py
Exits with code 1 if discovery or a call fails.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hr_agent.agent.mcp_client import MCPToolClient  # noqa: E402

REQUIRED = {"search_policy_documents", "lookup_employee_profile", "check_pto_balance",
            "check_policy_compliance", "create_mock_hr_ticket"}


async def main() -> int:
    client = MCPToolClient()
    await client.start()
    try:
        names = sorted(client.tools)
        print(f"Discovered {len(names)} MCP tools: {', '.join(names)}")
        missing = REQUIRED - set(names)
        if missing:
            print(f"FAIL: missing tools {missing}")
            return 1
        search = await client.call("search_policy_documents", {"query": "work from abroad limit", "k": 2})
        balance = await client.call("check_pto_balance", {"employee_id": "E1002"})
        print("search_policy_documents ->", json.dumps([r["citation"] for r in search.output.get("results", [])]))
        print("check_pto_balance ->", json.dumps({k: balance.output.get(k) for k in ("employee_id", "available_days")}))
        if not (search.ok and balance.ok):
            print("FAIL: tool call returned an error")
            return 1
        print("OK")
        return 0
    finally:
        await client.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
