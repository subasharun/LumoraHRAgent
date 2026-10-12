# AI tooling used

> Edit this file so it describes your own process accurately. The project rules require you to describe how you used AI tools and to stay responsible for the correctness, security, and academic integrity of the submission.

## Tools

| Tool | Used for |
|---|---|
| **Claude (Anthropic), in claude.ai with code execution** | Turning the assignment brief into an architecture; generating the synthetic policy corpus and mock data; writing the RAG pipeline, MCP server, rules engine, orchestrator, web UI, tests, CI workflow, evaluation harness, and first drafts of the documentation; running the tests and evaluation in a sandbox and iterating on failures. |
| **Groq API (free tier)** | Runtime LLM for the agent's planning and answers (not a coding tool). |
| *Add any others you used* (e.g. Claude Code, Cursor, Copilot, ChatGPT) | |

## How the AI assistant was used

1. **Requirements → design.** The PDF brief was given to Claude, which proposed the single-service free-tier architecture (FastAPI + orchestrator + stdio MCP server + FAISS + JSON mock data), the tool list, and the two demo workflows.
2. **Content generation.** The 14 policy documents and the mock datasets were generated with AI assistance, then cross-checked so that the numbers in the documents, the rules engine and the evaluation gold answers agree (for example the 20-workday work-from-abroad limit and the PTO notice table).
3. **Code generation with tests in the loop.** Each component was written and immediately exercised: parsers and chunking against the real corpus, MCP tools through a real MCP client session, the agent end to end, then pytest. Failures were fed back and fixed.
4. **Evaluation-driven iteration.** The evaluation harness exposed concrete weaknesses, which were fixed and re-measured: personal-intent detection firing on general questions, a section lookup where "4" matched "3.4", citations to parent sections with no text, and list fragments being quoted as facts. A held-out set was then run once to check for overfitting (results in `design-and-evaluation.md` §9.6).

## What worked well

- **Fast scaffolding of a lot of surface area** (corpus, server, agent, UI, CI, docs) while keeping a consistent structure and naming.
- **Running code instead of trusting it.** Executing every piece in a sandbox caught real problems early — for example, the installed MCP Python SDK was version 2, where `FastMCP` had been renamed to `MCPServer`; the import error surfaced immediately and the code was written against the new API.
- **Test doubles for unavailable services.** The sandbox could not reach Groq, so a fake OpenAI-compatible server was written to test the LLM tool-calling loop (tool discovery, MCP execution, confirmation stripping, citation removal) deterministically; the same test runs in CI.
- **Deterministic evaluation.** Pinning the date (`APP_TODAY`) and using an offline planner and TF-IDF embeddings made results reproducible in CI.

## What did not work well / needed human judgement

- **No live access to Groq or Hugging Face from the sandbox**, so LLM-mode quality and the fastembed embedding model were not measured there. These must be run after deployment with real keys (`python evaluation/run_eval.py --mode llm`).
- **Heuristics tuned on the dev set overfit.** Several regex-based intent rules and the "not covered" detector were adjusted while looking at dev-set failures; the held-out run found a false "not covered" (the word "night" vs "nightly") and an unhandled vague request. Generated heuristics need independent test data.
- **Free-tier constraints are easy to overlook.** The first version sent full JSON schemas to the LLM; token budgets on the free tier required compacting schemas and trimming tool results.
- **Metric design needed care.** The first groundedness metric counted boilerplate sentences as claims and compared "25.0" with "25" as different numbers; both were corrected and the definitions documented.
- **Everything generated was reviewed**: policy text for internal consistency, rules for off-by-one date logic (notice is counted in business days strictly between the request and the first day off), and security-relevant behaviour (no ticket creation without confirmation, privacy checks before any tool call).

## Responsibility

All code and content were reviewed, run, and tested before submission. Mock data is synthetic. Secrets are only read from environment variables.
