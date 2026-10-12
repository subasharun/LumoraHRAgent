"""Write the MCP tool definitions (as discovered via tools/list) to mcp_server/tool_schemas.json.

    python scripts/export_tool_schemas.py

The JSON is generated from the running server, so the documentation always matches the code.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hr_agent.agent.mcp_client import MCPToolClient  # noqa: E402


async def main() -> None:
    client = MCPToolClient()
    await client.start()
    try:
        tools = [{"name": t.name, "description": " ".join(t.description.split()), "input_schema": t.input_schema}
                 for t in client.tools.values()]
    finally:
        await client.stop()
    out = ROOT / "mcp_server" / "tool_schemas.json"
    out.write_text(json.dumps({"server": "lumora-hr", "tools": tools}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(tools)} tool schemas to {out}")


if __name__ == "__main__":
    asyncio.run(main())
