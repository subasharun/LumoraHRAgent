# Deployment

| | |
|---|---|
| **Deployed app** | `https://<your-service>.onrender.com` ← replace after deploying |
| **Health endpoint** | `https://<your-service>.onrender.com/health` |
| **API docs** | `https://<your-service>.onrender.com/docs` |
| **Platform** | Render free web service (blueprint: `render.yaml`) |
| **Planner** | LLM (`openai/gpt-oss-120b` on Groq free tier) when `LLM_API_KEY` is set; otherwise the offline rules planner |

## What `/health` returns

```json
{
  "status": "ok",
  "agent_mode": "llm",
  "llm": {"provider_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "configured": true},
  "mcp": {"transport": "stdio", "connected": true, "server": "lumora-hr",
          "tools": ["check_policy_compliance", "check_pto_balance", "create_mock_hr_ticket", "draft_hr_email",
                    "get_policy_section", "list_policy_documents", "lookup_benefits_status",
                    "lookup_employee_profile", "search_policy_documents"],
          "ping_ms": 2.0}
}
```

`status` is `degraded` if the MCP server is not connected.

## Free-tier cold starts

- Render free web services **sleep after about 15 minutes without traffic**. The next request wakes the service; expect roughly 30–60 seconds before the first response. The chat UI shows a "cold start, please retry" hint if that first request times out.
- After waking, the app starts the MCP server and loads the prebuilt index in about 3 seconds (measured locally: startup to healthy 3.2 s; first chat request ~100 ms with the rules planner).
- The vector index and embedding model are built and downloaded during the **build**, not at request time.
- Before a demo or grading session, open `/health` once and wait for `"connected": true`.
- Mock tickets and chat sessions are kept in memory/local files and reset when the instance restarts or redeploys (expected for a demo).

## Free-tier LLM limits

The Groq free tier limits tokens per minute. If a request is rate-limited, the agent retries once and then answers with the offline rules planner; the trace shows an `llm_unavailable` step. Waiting a minute restores LLM mode.

## Deploy checklist

1. Render → **New → Blueprint** → select the repository.
2. Set `LLM_API_KEY` in the service environment.
3. First deploy from the dashboard; afterwards deployments come from GitHub Actions via the deploy hook (`RENDER_DEPLOY_HOOK_URL` secret) and only after tests pass.
4. Set the GitHub variable `DEPLOYED_URL` so CI checks `/health` after each deploy.
5. Fill in the URLs at the top of this file and in the README.
