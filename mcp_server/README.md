# MCP server: `lumora-hr`

The HR tools the agent uses, exposed over the Model Context Protocol with the official Python SDK (`mcp` 2.x, `MCPServer`).

## Run

```bash
python -m mcp_server.server                                   # stdio (what the web app uses; started automatically)
python -m mcp_server.server --transport http --port 8765      # Streamable HTTP at http://localhost:8765/mcp
python scripts/mcp_smoke_test.py                              # discover tools + call two of them
python scripts/export_tool_schemas.py                         # refresh tool_schemas.json from the live server
```

To use the HTTP server from the web app: `MCP_TRANSPORT=http MCP_SERVER_URL=http://localhost:8765/mcp`.

It also works with any MCP client, e.g. Claude Desktop (`"command": "python", "args": ["-m", "mcp_server.server"]`, with the repository as the working directory).

## Files

| File | Purpose |
|---|---|
| `server.py` | Tool definitions (typed arguments with descriptions → JSON schemas), transports |
| `compliance.py` | Rules engine behind `check_policy_compliance`; every check carries a policy citation |
| `data.py` | Reads `mock_data/`, business-day arithmetic, mock ticket store |
| `tool_schemas.json` | Tool names, descriptions and input schemas exactly as returned by `tools/list` |

## Tools

| Tool | Kind | Notes |
|---|---|---|
| `search_policy_documents` | RAG | Top-k chunks with citation + snippet; also `keyword_coverage` and `unmatched_terms` for scope checks |
| `get_policy_section` | RAG | Full text of one section (`"4"`, `"5.1"`, or heading words) |
| `list_policy_documents` | RAG | Document IDs, titles, sections |
| `lookup_employee_profile` | mock data | Employment type, FTE, tenure, arrangement, location, manager |
| `check_pto_balance` | mock data | Available, pending, entitlement, cap, carryover |
| `lookup_benefits_status` | mock data | Eligibility by program + elections |
| `check_policy_compliance` | rules | `pto`, `remote_work_domestic`, `work_from_abroad`, `expense` |
| `create_mock_hr_ticket` | mock action | Preview unless `confirm=true`; the agent only confirms after the user does |
| `draft_hr_email` | mock action | Returns a draft; never sends |

Errors are returned as `{"error": ..., "message": ...}` so the client can explain them. Logging goes to stderr because stdout carries the stdio protocol.
