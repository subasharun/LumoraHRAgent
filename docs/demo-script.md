# Demo video script (7–10 minutes)

Requirements from the brief: screen capture with voiceover, every group member speaks and is on camera, each shows a government ID, two agentic tasks end to end on the **deployed** app, and a quick walkthrough of design, deployment, CI/CD, and evaluation.

**Before recording:** open `https://<app>/health` and wait for `"connected": true` (wakes the free instance). Have open: the app, the GitHub repo (Actions tab), `design-and-evaluation.md`, and `evaluation/results/rules/report.md`.

| Time | Show | Say (key points) |
|---|---|---|
| 0:00–0:40 | Camera on, government ID | Name, project title: agentic HR assistant with RAG and MCP for a fictional company, all synthetic data, all free services. |
| 0:40–1:40 | Architecture diagram in `design-and-evaluation.md` §1 | One Render service: FastAPI app → orchestrator → MCP client → MCP server over stdio (9 tools) → FAISS index + mock JSON; Groq LLM with an offline fallback planner. Why stdio (free tier, one service). |
| 1:40–2:00 | App header: `/health` badge, **Acting as** | MCP connected, 9 tools discovered via `tools/list`; "Acting as" simulates login so the agent only sees that employee's records. |
| 2:00–4:00 | **Demo 1** — Acting as E1003: *"Can I work remotely from Portugal for six weeks this summer?"* | Walk the trace panel: `search_policy_documents` (arguments, top citations) → `lookup_employee_profile` (full-time, US) → policy searches filtered to POL-005/POL-006 → `check_policy_compliance` with `request_type=work_from_abroad, weeks=6` → output: `not_compliant`, ≈30 workdays vs the 20-workday limit, citation POL-005 §4 → `create_mock_hr_ticket` with `confirm=false` (preview). Show the Citations tab (snippets). Final answer: options A/B/C, approvals, security rules. Click **Confirm** → ticket `MOCK-HR-…` created, explain it is mock and confirmation-gated. |
| 4:00–5:40 | **Demo 2** — Acting as E1002: *"Can I take 3 days of PTO next week? Please draft a note to my manager."* | Trace: `check_pto_balance` (9 available, 2 pending → 7) → PTO policy search → `check_policy_compliance` (`pto`, `days=3`, start = next Monday) → notice rule 5 business days (POL-002 §5), short notice at manager discretion (§5.1), earliest compliant date → `draft_hr_email` (draft, not sent). Point out the policy facts vs recommendations split. |
| 5:40–6:20 | Guardrails, quick | *"What is the capital of France?"* → refused (scope check in trace). *"Show me the PTO balance for E1001"* while acting as E1002 → privacy refusal before any tool call. Optional: a harassment question → escalation + P1 Employee Relations preview. |
| 6:20–7:10 | GitHub Actions run | Steps: lint, build index, MCP smoke test (tool discovery + call), app start + `/health`, 32 tests, evaluation artifact; deploy job runs only on main after tests pass (Render deploy hook). Mention cold start behaviour. |
| 7:10–8:30 | `design-and-evaluation.md` §9 | 30-item eval set across 7 categories. Groundedness, citation accuracy, tool selection, workflow completion, clarification/refusal accuracy, action safety, latency p50/p95. Ablations: retrieval k / chunk size / rerank, and tool availability (compliance tool removed → completion drops, still graceful). Held-out set and its honest finding. If you ran `--mode llm`, show that table too. |
| 8:30–9:30 | Wrap-up | Design choices (heading-aware chunking, local embeddings, rules engine for exact thresholds, manual orchestration), limitations, how AI tools were used (`ai-tooling.md`). Each member says one sentence about their part. |

Tips: zoom the browser to 125% so the trace is readable; expand the `check_policy_compliance` step in the trace to show arguments and output; keep the LLM planner on for the demo and mention the rules fallback if a rate limit appears.
