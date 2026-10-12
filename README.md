# Lumora HR Agent — agentic RAG + MCP

An agentic HR assistant for **Lumora Systems**, a fictional company. It answers HR policy questions with cited sources (RAG) and completes multi-step HR tasks — remote-work eligibility, PTO requests, expense checks, benefits triage, sensitive-case triage — by calling tools exposed through a **Model Context Protocol (MCP)** server over synthetic employee data.

Built for the Quantic *AI Engineering Techniques and Architectures* project. Everything runs on free services. All data is synthetic.

**Deployed app:** see [`deployed.md`](deployed.md) · **Design and evaluation:** [`design-and-evaluation.md`](design-and-evaluation.md) · **AI tooling:** [`ai-tooling.md`](ai-tooling.md)

## What it does

| Capability | How |
|---|---|
| Policy Q&A with citations | 14 policy documents (Markdown, HTML, TXT, PDF; ~13.6k words) → heading-aware chunks → local embeddings → FAISS. Answers cite `[POL-002 §5]` with supporting snippets. |
| Agentic workflows | The orchestrator detects intent, picks MCP tools, calls them, checks rules, and writes a grounded answer. Two planners: an LLM with function calling (Groq free tier) or a deterministic offline planner (no API key). |
| MCP integration | 9 tools on one MCP server (stdio by default, Streamable HTTP optional). The agent discovers tools via `tools/list`; every call goes through `tools/call`. |
| Safety | Out-of-scope refusal, privacy (own records only), escalation of harassment/safety topics, prompt-injection handling, confirmation-gated mock tickets, email drafts never sent, citation verification. |
| Visible trace | Every response returns an operational trace: guardrail decisions, tool names, arguments, outputs, timings, sources, and escalation. |

## Architecture (one free Render service)

```
Browser ──► FastAPI web app (/, /chat, /health)
               │
               ▼
        Agent orchestrator ── guardrails · intent · citation verification
         │            │
         │            └─► LLM planner (Groq, OpenAI-compatible)  ─or─  offline rules planner
         ▼
        MCP client ──stdio──► MCP server "lumora-hr" (child process)
                                 ├─ RAG tools ──► FAISS index + chunk metadata (corpus/)
                                 ├─ data tools ─► mock_data/*.json (synthetic)
                                 ├─ rules engine (check_policy_compliance)
                                 └─ mock actions (ticket preview/confirm, email draft)
```

Details, diagram, and design choices: [`design-and-evaluation.md`](design-and-evaluation.md).

## Free services used

| Need | Choice | Cost |
|---|---|---|
| LLM | Groq API free tier (`openai/gpt-oss-120b`), or any OpenAI-compatible free option (OpenRouter `:free`, Gemini, local Ollama) | $0 |
| Embeddings | `BAAI/bge-small-en-v1.5` via `fastembed` (local ONNX, no key); TF-IDF fallback | $0 |
| Vector store | FAISS (in-process, built at deploy time) | $0 |
| Hosting | Render free web service | $0 |
| CI/CD | GitHub Actions + Render deploy hook | $0 |

No API key? The app still works: it uses the deterministic offline planner (`AGENT_MODE=rules`).

## Quick start (local)

Requires Python 3.11+ (3.13 is what CI and Render use).

```bash
git clone <your-repo-url> && cd hr-agent
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                         # optional: add LLM_API_KEY (free Groq key)
python -m hr_agent.rag.build_index                           # builds data/index/ (downloads ~67 MB model once)
uvicorn hr_agent.web.app:app --reload --port 8000
```

Open http://localhost:8000, pick an employee in **Acting as**, and click a **▶ Demo** button.

No network for the model download? Use `EMBEDDING_BACKEND=tfidf` (no download). The index is also built automatically on first start if it is missing.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example) and [`hr_agent/config.py`](hr_agent/config.py)). Secrets are never committed; `.env` is git-ignored.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_API_KEY` | empty | Groq (or other) key. Empty → offline rules planner. |
| `LLM_BASE_URL` / `LLM_MODEL` | Groq / `openai/gpt-oss-120b` | Any OpenAI-compatible endpoint and model with tool calling. |
| `AGENT_MODE` | `auto` | `auto` (LLM if key), `llm`, or `rules`. |
| `EMBEDDING_BACKEND` | `fastembed` | `fastembed` or `tfidf`. |
| `RETRIEVAL_K` | `5` | Chunks returned per search. |
| `CHUNK_MAX_WORDS` / `CHUNK_OVERLAP_WORDS` | `220` / `40` | Chunking. |
| `MCP_TRANSPORT` | `stdio` | `stdio` (child process) or `http` (separate MCP service at `MCP_SERVER_URL`). |
| `APP_TODAY` | empty | Pin "today" for reproducible date rules (evaluation uses `2026-10-12`). |
| `SEED` | `42` | Passed to the LLM; chunking and evaluation are deterministic. |

## Reproduce the two demo tasks

**In the UI:** click **▶ Demo: Remote work eligibility** and **▶ Demo: PTO request guidance**. The right panel shows the MCP tool-call trace and the citations; the first task ends with a **Confirm** button for the mock ticket.

**From an API client:**

```bash
./scripts/demo_requests.sh                                  # local
./scripts/demo_requests.sh https://<your-app>.onrender.com  # deployed
```

or directly:

```bash
curl -X POST http://localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"Can I work remotely from Portugal for six weeks this summer?","employee_id":"E1003"}'
curl -X POST http://localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"Can I take 3 days of PTO next week? Please draft a note to my manager.","employee_id":"E1002"}'
```

`/chat` returns `answer`, `citations` (doc ID, section, snippet), `trace` (tool calls with arguments and outputs), `pending_action`, `escalation`, and `latency_ms`. Confirm a pending mock action with `{"session_id": ..., "confirm_action_id": ...}`.

Other endpoints: `GET /health` (app + MCP status), `GET /api/tools` (tools discovered over MCP), `GET /api/demo-tasks`, `GET /docs` (OpenAPI).

## Tests and CI/CD

```bash
pytest                              # 32 tests: RAG, rules, MCP discovery/calls, app start, both demo tasks, LLM loop (fake LLM)
python scripts/mcp_smoke_test.py    # standalone MCP discovery + tool-call check
ruff check .
```

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request: install → lint → build index → import check → MCP smoke test → start the app and check `/health` → pytest → evaluation (report uploaded as an artifact). The **deploy** job runs only on `main` and only if all of that passed: it calls the Render deploy hook and then polls the deployed `/health`.

## Evaluation

```bash
python evaluation/run_eval.py --mode rules --ablations --cold-start   # offline, deterministic
python evaluation/run_eval.py --mode llm                              # with LLM_API_KEY set
python evaluation/run_eval.py --base-url https://<your-app>.onrender.com   # deployed app over HTTP
```

30 questions/tasks with gold answers ([`evaluation/eval_set.json`](evaluation/eval_set.json)) plus a 10-item held-out set. Results and the ablations are in [`evaluation/results/`](evaluation/results/) and summarised in [`design-and-evaluation.md`](design-and-evaluation.md#9-evaluation).

## Deploy to Render (free)

1. Push this repository to GitHub and share it with `quantic-grader`.
2. Get a free Groq API key at https://console.groq.com/keys (optional but recommended).
3. In Render: **New → Blueprint**, select the repo. Render reads [`render.yaml`](render.yaml) and creates one free web service.
4. In the service's **Environment**, set `LLM_API_KEY`.
5. Deploy once manually from the Render dashboard. Build: `pip install -r requirements.txt && python -m hr_agent.rag.build_index`; start: `uvicorn hr_agent.web.app:app --host 0.0.0.0 --port $PORT`.
6. For CI-gated deploys: copy the service's **Deploy Hook** URL into the GitHub secret `RENDER_DEPLOY_HOOK_URL`, and the app URL into the GitHub variable `DEPLOYED_URL`. `autoDeploy` is off in `render.yaml`, so deployments happen only after tests pass.
7. Put the URL in [`deployed.md`](deployed.md).

**Cold starts:** the free instance sleeps after ~15 minutes without traffic. The first request after that wakes it (typically 30–60 s; the UI tells the user to retry if it times out). Once awake, startup to healthy takes ~3 s and requests take tens of milliseconds plus LLM time. Open `/health` a minute before a demo.

Railway works too: the [`Procfile`](Procfile) builds the index and starts the app.

## Repository layout

```
corpus/                 14 synthetic policy documents (.md, .html, .txt, .pdf), the policy corpus
mock_data/              synthetic employees, PTO balances, benefits, tickets, holidays (JSON)
mcp_server/             MCP server (server.py), rules engine (compliance.py), data access, tool_schemas.json
                        (the brief's "mcp/" folder; named mcp_server so it does not shadow the `mcp` SDK package)
hr_agent/rag/           parsers, chunking, embeddings, FAISS index, query rewriting
hr_agent/agent/         orchestrator, MCP client, LLM client, guardrails, intent/argument extraction, prompts
hr_agent/web/           FastAPI app and the chat UI (static/index.html)
evaluation/             eval set, held-out set, run_eval.py, results/
tests/                  pytest suite
scripts/                MCP smoke test, demo requests, tool-schema export, PDF generator
docs/demo-script.md     7–10 minute demo video script
```

## Data notice

All people, IDs, policies, and numbers are invented for a course project. No real or private employee information is used.
