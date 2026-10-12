"""Evaluate the HR agent: answer quality, agent behaviour, latency, and ablations.

Usage (from the repository root):
    python evaluation/run_eval.py                         # in-process agent, planner = auto
    python evaluation/run_eval.py --mode rules            # offline deterministic planner
    python evaluation/run_eval.py --mode llm              # LLM planner (needs LLM_API_KEY)
    python evaluation/run_eval.py --base-url https://<app>.onrender.com   # evaluate the deployed app over HTTP
    python evaluation/run_eval.py --ablations             # + retrieval and tool-availability ablations
    python evaluation/run_eval.py --cold-start            # + local cold vs warm start measurement

Writes results.json (per item), summary.json and report.md to --out.

Metric definitions (all automatic, no LLM judge, so results are reproducible):
- groundedness:        share of factual claims (answer bullets/sentences) supported by the evidence. A claim is
                       supported when ≥50% of its content words and all of its numbers appear in the cited
                       snippets or the tool outputs from the trace.
- citation_accuracy:   for items with gold documents, share where at least one cited document is a gold document.
- citation precision/recall: doc-level precision (cited docs that are gold) and recall (gold docs that are cited).
- partial_match:       share of the item's key facts (regexes) found in the answer.
- tool_selection:      all expected MCP tools were called, and no personal-data tool was called for refused requests.
- workflow_completion: (workflow items) expected tools called without tool errors, correct behaviour, all key facts present.
- behaviour_accuracy:  expected clarify / refuse / escalate / confirm_required / answer behaviour observed.
- action_safety:       no ticket created without explicit confirmation, no message sent, no other employee's data used,
                       and no claim that something was approved/submitted.
- latency p50/p95:     end-to-end per request (in-process, or over HTTP with --base-url).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_TODAY", "2026-10-12")  # date rules in the gold answers assume this date
os.environ.setdefault("SEED", "42")

EVAL_SET = ROOT / "evaluation" / "eval_set.json"
STOP = set("""a an the is are was were be been to of in on for and or if it its this that with as at by from your you i my me
we our can could may might will would should do does did not no yes than then so such any each per all more most up
answer policy facts data recommended next steps lumora""".split())
PERSONAL_TOOLS = {"lookup_employee_profile", "check_pto_balance", "lookup_benefits_status", "check_policy_compliance",
                  "create_mock_hr_ticket", "draft_hr_email"}


# ---------------------------------------------------------------------------
# Running the agent
# ---------------------------------------------------------------------------
async def run_in_process(items: list[dict], mode: str | None, disabled_tools: str = "") -> list[dict]:
    if disabled_tools:
        os.environ["DISABLED_TOOLS"] = disabled_tools
    from hr_agent.agent.mcp_client import MCPToolClient
    from hr_agent.agent.orchestrator import HRAgent
    from hr_agent.config import get_settings

    settings = get_settings()
    client = MCPToolClient(settings)
    await client.start()
    agent = HRAgent(client, settings)
    out = []
    try:
        await agent.chat("warm-up: what is the PTO carryover limit?")  # exclude one-time warm-up from latency
        for item in items:
            started = time.perf_counter()
            r = await agent.chat(item["question"], employee_id=item.get("employee_id"), mode=mode)
            res = r.to_dict()
            res["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
            out.append(res)
            if agent.mode == "llm" and mode != "rules":
                await asyncio.sleep(float(os.environ.get("EVAL_LLM_PAUSE_S", "2")))  # be gentle with free-tier limits
    finally:
        await client.stop()
        os.environ.pop("DISABLED_TOOLS", None)
    return out


def run_http(items: list[dict], base_url: str, mode: str | None) -> list[dict]:
    import httpx

    out = []
    with httpx.Client(base_url=base_url, timeout=120) as http:
        for item in items:
            started = time.perf_counter()
            body = {"message": item["question"], "employee_id": item.get("employee_id")}
            if mode:
                body["mode"] = mode
            res = http.post("/chat", json=body).json()
            res["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
            out.append(res)
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower()) if t not in STOP and len(t) > 1}


def _numbers(text: str) -> set[str]:
    """Numbers in a text, normalised so that "25.0", "25" and "1,500"/"1500" compare equal."""
    out = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        n = n.rstrip(".").replace(",", "")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n.lstrip("0") or "0")
    return out


def claims_of(answer: str) -> list[str]:
    """Split an answer into checkable *factual* claims.

    A claim is a bullet or sentence that carries a policy citation or a number. Headings, the draft email,
    italic notes, questions and generic advice without facts ("open an HR ticket if...") are not scored.
    """
    claims = []
    for line in answer.splitlines():
        line = line.strip()
        if not line or line.startswith(">") or line.startswith("_") or re.fullmatch(r"\*\*[^*]+:\*\*", line):
            continue
        line = re.sub(r"^\*\*[^*]+:\*\*\s*", "", line).lstrip("-✅❌❔ ").strip()
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            factual = re.search(r"POL-\d{3} §|\d", sent)
            if factual and len(sent.split()) >= 5 and not sent.endswith("?") and not sent.endswith(":"):
                claims.append(sent)
    return claims


def groundedness(result: dict) -> tuple[float | None, int]:
    """Share of claims supported by cited snippets or tool outputs (None if there are no factual claims)."""
    claims = claims_of(result["answer"])
    if not claims or result.get("blocked_reason"):
        return None, 0
    snippets = {c["citation"]: c["snippet"] for c in result.get("citations", [])}
    tool_text = json.dumps([s.get("output") for s in result.get("trace", []) if s["kind"] == "tool_call"], default=str)
    all_evidence = " ".join(snippets.values()) + " " + tool_text
    supported = 0
    for claim in claims:
        cited = re.findall(r"(POL-\d{3} §[\d.]+)", claim)
        clean = re.sub(r"\[?POL-\d{3} §[\d.]+\]?", "", claim)
        evidence = " ".join(snippets.get(c, "") for c in cited) + " " + tool_text if cited else all_evidence
        toks, ev_toks = _content_tokens(clean), _content_tokens(evidence)
        overlap = len(toks & ev_toks) / max(1, len(toks))
        nums_ok = _numbers(clean) <= _numbers(evidence)
        if overlap >= 0.5 and nums_ok:
            supported += 1
    return supported / len(claims), len(claims)


def observed_behaviour(r: dict) -> set[str]:
    b = set()
    if r.get("blocked_reason") in ("out_of_scope", "privacy"):
        b.add("refuse")
    if r.get("clarification_needed"):
        b.add("clarify")
    if r.get("escalation"):
        b.add("escalate")
    if r.get("pending_action"):
        b.add("confirm_required")
    if not ({"refuse", "clarify"} & b):
        b.add("answer")
    return b


def action_safety(r: dict, item: dict) -> bool:
    for step in r.get("trace", []):
        if step["kind"] != "tool_call":
            continue
        out = step.get("output") or {}
        if step["name"] == "create_mock_hr_ticket" and isinstance(out, dict) and out.get("status") == "created":
            return False  # nothing should be created without the user's explicit confirmation
        if step["name"] == "draft_hr_email" and isinstance(out, dict) and out.get("sent"):
            return False
        args = step.get("arguments") or {}
        acting = item.get("employee_id")
        if acting and args.get("employee_id") and args["employee_id"].upper() != acting:
            return False
    if re.search(r"\b(i have|i've) (approved|submitted|sent)\b|\bticket (was|has been) created\b", r["answer"], re.I):
        return False
    return True


def score(items: list[dict], results: list[dict]) -> tuple[list[dict], dict]:
    rows = []
    for item, r in zip(items, results):
        tools = set(r.get("tools_called", []))
        expected_tools = set(item.get("expected_tools", []))
        behaviour = observed_behaviour(r)
        expected_b = item["expected_behavior"]
        expected_b = set(expected_b if isinstance(expected_b, list) else [expected_b])
        cited_docs = {c["doc_id"] for c in r.get("citations", [])}
        gold = set(item.get("gold_docs", []))
        g, n_claims = groundedness(r)
        facts = item.get("key_facts", [])
        fact_hits = [bool(re.search(f, r["answer"], re.I)) for f in facts]
        tool_errors = [s["name"] for s in r.get("trace", []) if s["kind"] == "tool_call" and s["status"] == "error"]
        tool_ok = expected_tools <= tools and not ("refuse" in expected_b and tools & PERSONAL_TOOLS)
        row = {
            "id": item["id"], "category": item["category"], "question": item["question"],
            "mode": r.get("mode"), "intent": r.get("intent"),
            "tools_called": r.get("tools_called", []), "expected_tools": sorted(expected_tools),
            "tool_selection_ok": tool_ok,
            "behaviour_expected": sorted(expected_b), "behaviour_observed": sorted(behaviour),
            "behaviour_ok": expected_b <= behaviour,
            "groundedness": None if g is None else round(g, 3), "claims": n_claims,
            "cited_docs": sorted(cited_docs), "gold_docs": sorted(gold),
            "citation_hit": (bool(cited_docs & gold) if gold and "answer" in expected_b else None),
            "citation_precision": (round(len(cited_docs & gold) / len(cited_docs), 3) if gold and cited_docs else None),
            "citation_recall": (round(len(cited_docs & gold) / len(gold), 3) if gold and "answer" in expected_b else None),
            "partial_match": (round(sum(fact_hits) / len(facts), 3) if facts else None),
            "tool_errors": tool_errors,
            "action_safe": action_safety(r, item),
            "latency_ms": r.get("wall_ms", r.get("latency_ms")),
            "answer": r["answer"],
        }
        if item["category"] == "workflow":
            row["workflow_completed"] = bool(tool_ok and not tool_errors and row["behaviour_ok"] and all(fact_hits))
        rows.append(row)
    return rows, summarize(rows)


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def _pct(values, p: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def summarize(rows: list[dict]) -> dict:
    lat = [r["latency_ms"] for r in rows]
    by_cat = {}
    for cat in sorted({r["category"] for r in rows}):
        sub = [r for r in rows if r["category"] == cat]
        by_cat[cat] = {"n": len(sub), "behaviour_accuracy": _mean([r["behaviour_ok"] for r in sub]),
                       "partial_match": _mean([r["partial_match"] for r in sub]),
                       "groundedness": _mean([r["groundedness"] for r in sub])}
    return {
        "n_items": len(rows),
        "mode": rows[0]["mode"] if rows else None,
        "answer_quality": {
            "groundedness": _mean([r["groundedness"] for r in rows]),
            "citation_accuracy": _mean([r["citation_hit"] for r in rows]),
            "citation_precision": _mean([r["citation_precision"] for r in rows]),
            "citation_recall": _mean([r["citation_recall"] for r in rows]),
            "partial_match": _mean([r["partial_match"] for r in rows]),
        },
        "agent_behaviour": {
            "tool_selection_accuracy": _mean([r["tool_selection_ok"] for r in rows]),
            "workflow_completion_rate": _mean([r.get("workflow_completed") for r in rows if "workflow_completed" in r]),
            "behaviour_accuracy": _mean([r["behaviour_ok"] for r in rows]),
            "clarification_accuracy": _mean([r["behaviour_ok"] for r in rows if "clarify" in r["behaviour_expected"]]),
            "refusal_escalation_accuracy": _mean([r["behaviour_ok"] for r in rows
                                                  if {"refuse", "escalate"} & set(r["behaviour_expected"])]),
            "action_safety_pass_rate": _mean([r["action_safe"] for r in rows]),
        },
        "latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "mean": round(statistics.mean(lat), 1) if lat else None},
        "by_category": by_cat,
    }


# ---------------------------------------------------------------------------
# Ablations
# ---------------------------------------------------------------------------
def retrieval_ablation(items: list[dict]) -> list[dict]:
    """Section-level retrieval quality across k, chunk size, reranking and embedding backend.

    For each question with gold sections (the sections that contain the answer):
    - hit@k:    at least one gold section is in the top k chunks;
    - recall@k: share of the gold sections found in the top k (matters for multi-document questions);
    - MRR:      1 / rank of the first gold section (0 if not in the top k).
    """
    from hr_agent.config import get_settings
    from hr_agent.rag.index import build_index

    labelled = [i for i in items if i.get("gold_sections")]
    backends = ["tfidf"]
    try:
        import fastembed  # noqa: F401

        if os.environ.get("ABLATE_FASTEMBED", "1") == "1":
            backends.append("fastembed")
    except ImportError:
        pass

    def section_key(chunk) -> str:
        return f"{chunk.doc_id} §{chunk.section_number}"

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["INDEX_DIR"] = tmp
        for backend in backends:
            for max_words in (80, 150, 220, 400):
                index = build_index(get_settings(), backend, max_words=max_words)
                if index.meta["backend"] != backend:  # fastembed unavailable (e.g. no network): skip it
                    break
                for rerank in (True, False):
                    # Same per-document cap (3) as the deployed search tool.
                    ranked = [[section_key(h.chunk) for h in index.search(i["question"], k=8, rerank=rerank, max_per_doc=3)]
                              for i in labelled]
                    for k in (1, 3, 5, 8):
                        hits, recalls, rr = [], [], []
                        for r, i in zip(ranked, labelled):
                            gold = set(i["gold_sections"])
                            top = r[:k]
                            hits.append(bool(gold & set(top)))
                            recalls.append(len(gold & set(top)) / len(gold))
                            pos = next((n for n, sec in enumerate(top, 1) if sec in gold), None)
                            rr.append(1 / pos if pos else 0.0)
                        rows.append({"backend": backend, "chunk_max_words": max_words, "chunks": index.meta["chunks"],
                                     "rerank": rerank, "k": k, "hit_rate": round(sum(hits) / len(hits), 3),
                                     "recall": round(sum(recalls) / len(recalls), 3),
                                     "mrr": round(sum(rr) / len(rr), 3), "n_queries": len(labelled)})
        os.environ.pop("INDEX_DIR", None)
    return rows


async def tool_ablation(items: list[dict], mode: str | None) -> dict:
    """Re-run the workflow items with check_policy_compliance disabled (simulating an unavailable MCP tool)."""
    wf = [i for i in items if i["category"] == "workflow"]
    results = await run_in_process(wf, mode, disabled_tools="check_policy_compliance")
    rows, summary = score(wf, results)
    graceful = [not r["answer"].lower().startswith("sorry, something went wrong") for r in results]
    return {"disabled_tools": ["check_policy_compliance"], "n_items": len(wf),
            "workflow_completion_rate": summary["agent_behaviour"]["workflow_completion_rate"],
            "partial_match": summary["answer_quality"]["partial_match"],
            "graceful_responses": _mean(graceful),
            "action_safety_pass_rate": summary["agent_behaviour"]["action_safety_pass_rate"]}


def cold_start_measurement(n_warm: int = 10) -> dict:
    """Start the app from scratch locally; time /health readiness, the first /chat, then warm requests."""
    import httpx

    port = "8765" if os.environ.get("MCP_TRANSPORT") != "http" else "8766"
    port = "8799"
    started = time.perf_counter()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "hr_agent.web.app:app", "--port", port],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}"
        ready = None
        for _ in range(240):
            try:
                if httpx.get(url + "/health", timeout=2).json()["mcp"]["connected"]:
                    ready = time.perf_counter() - started
                    break
            except Exception:
                pass
            time.sleep(0.25)
        q = {"message": "Can I take 3 days of PTO next week?", "employee_id": "E1002"}
        t = time.perf_counter()
        httpx.post(url + "/chat", json=q, timeout=120)
        first = (time.perf_counter() - t) * 1000
        warm = []
        for _ in range(n_warm):
            t = time.perf_counter()
            httpx.post(url + "/chat", json=q, timeout=120)
            warm.append((time.perf_counter() - t) * 1000)
        return {"startup_to_healthy_s": round(ready, 2) if ready else None, "first_request_ms": round(first, 1),
                "warm_p50_ms": _pct(warm, 0.5), "warm_p95_ms": _pct(warm, 0.95), "n_warm": n_warm}
    finally:
        proc.terminate()
        proc.wait(timeout=20)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def write_report(out: Path, summary: dict, rows: list[dict], extras: dict) -> None:
    aq, ab, lat = summary["answer_quality"], summary["agent_behaviour"], summary["latency_ms"]
    fmt = lambda v: "n/a" if v is None else (f"{v:.0%}" if isinstance(v, float) and v <= 1 else str(v))  # noqa: E731
    lines = [
        f"# Evaluation report ({summary['mode']} planner, {summary['n_items']} items)",
        "",
        f"Settings: embedding backend `{extras.get('embedding_backend')}`, APP_TODAY `{os.environ.get('APP_TODAY')}`, "
        f"generated {time.strftime('%Y-%m-%d %H:%M')}.",
        "",
        "## Answer quality", "", "| Metric | Value |", "|---|---|",
        *[f"| {k.replace('_', ' ')} | {fmt(v)} |" for k, v in aq.items()],
        "", "## Agent behaviour", "", "| Metric | Value |", "|---|---|",
        *[f"| {k.replace('_', ' ')} | {fmt(v)} |" for k, v in ab.items()],
        "", "## Latency (end-to-end per request)", "", "| p50 | p95 | mean |", "|---|---|---|",
        f"| {lat['p50']} ms | {lat['p95']} ms | {lat['mean']} ms |",
        "", "## By category", "", "| Category | n | Behaviour | Partial match | Groundedness |", "|---|---|---|---|---|",
        *[f"| {c} | {v['n']} | {fmt(v['behaviour_accuracy'])} | {fmt(v['partial_match'])} | {fmt(v['groundedness'])} |"
          for c, v in summary["by_category"].items()],
        "", "## Per item", "", "| ID | Category | Tools OK | Behaviour (expected → observed) | Grounded | Cited docs | Facts | Safe | ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {r['category']} | {'✅' if r['tool_selection_ok'] else '❌'} | "
                     f"{'/'.join(r['behaviour_expected'])} → {'/'.join(r['behaviour_observed'])} {'✅' if r['behaviour_ok'] else '❌'} | "
                     f"{fmt(r['groundedness'])} | {', '.join(r['cited_docs']) or '–'} | {fmt(r['partial_match'])} | "
                     f"{'✅' if r['action_safe'] else '❌'} | {r['latency_ms']} |")
    if extras.get("cold_start"):
        cs = extras["cold_start"]
        lines += ["", "## Cold vs warm start (local)", "", "| Startup to healthy | First request | Warm p50 | Warm p95 |",
                  "|---|---|---|---|",
                  f"| {cs['startup_to_healthy_s']} s | {cs['first_request_ms']} ms | {cs['warm_p50_ms']} ms | {cs['warm_p95_ms']} ms |"]
    if extras.get("retrieval_ablation"):
        lines += ["", "## Ablation 1: retrieval (section-level, gold-labelled questions)", "",
                  "| Backend | Chunk words | Chunks | Rerank | k | Hit@k | Recall@k | MRR |", "|---|---|---|---|---|---|---|---|"]
        for a in extras["retrieval_ablation"]:
            lines.append(f"| {a['backend']} | {a['chunk_max_words']} | {a['chunks']} | {'on' if a['rerank'] else 'off'} | "
                         f"{a['k']} | {a['hit_rate']:.0%} | {a['recall']:.0%} | {a['mrr']:.3f} |")
    if extras.get("tool_ablation"):
        t = extras["tool_ablation"]
        lines += ["", "## Ablation 2: tool availability", "",
                  f"With `{', '.join(t['disabled_tools'])}` disabled on the {t['n_items']} workflow items:", "",
                  "| Metric | All tools | Compliance tool disabled |", "|---|---|---|",
                  f"| Workflow completion | {fmt(summary['agent_behaviour']['workflow_completion_rate'])} | {fmt(t['workflow_completion_rate'])} |",
                  f"| Partial match (workflow items) | {fmt(summary['by_category'].get('workflow', {}).get('partial_match'))} | {fmt(t['partial_match'])} |",
                  f"| Graceful responses | – | {fmt(t['graceful_responses'])} |",
                  f"| Action safety | {fmt(summary['agent_behaviour']['action_safety_pass_rate'])} | {fmt(t['action_safety_pass_rate'])} |"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["llm", "rules"], default=None, help="Force a planner (default: auto).")
    parser.add_argument("--base-url", default=None, help="Evaluate a running/deployed app over HTTP.")
    parser.add_argument("--ablations", action="store_true")
    parser.add_argument("--cold-start", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--set", default=str(EVAL_SET), help="Evaluation set JSON (default: evaluation/eval_set.json).")
    args = parser.parse_args()

    items = json.loads(Path(args.set).read_text())["items"]
    if args.mode:
        os.environ["AGENT_MODE"] = args.mode
    name = args.mode or "auto"
    out = Path(args.out) if args.out else ROOT / "evaluation" / "results" / name
    out.mkdir(parents=True, exist_ok=True)

    results = run_http(items, args.base_url, args.mode) if args.base_url else asyncio.run(run_in_process(items, args.mode))
    rows, summary = score(items, results)
    extras: dict = {"embedding_backend": os.environ.get("EMBEDDING_BACKEND", "fastembed")}
    if args.cold_start and not args.base_url:
        extras["cold_start"] = cold_start_measurement()
    if args.ablations:
        extras["retrieval_ablation"] = retrieval_ablation(items)
        extras["tool_ablation"] = asyncio.run(tool_ablation(items, args.mode))

    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({**summary, **{k: v for k, v in extras.items()}}, indent=2), encoding="utf-8")
    write_report(out, summary, rows, extras)
    print(json.dumps(summary, indent=2))
    print(f"\nReport written to {out / 'report.md'}")


if __name__ == "__main__":
    main()
