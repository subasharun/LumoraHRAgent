"""The agent orchestrator: interpret intent → select tools → call them via MCP → answer with citations.

One request goes through these stages (each one is recorded in the trace the UI shows):

  1. Confirmation handling  – if the user is confirming a pending mock action, execute it and stop.
  2. Guardrails (pre)       – emergency, privacy, sensitive-topic and prompt-injection checks.
  3. Evidence + scope       – an initial `search_policy_documents` MCP call. Its keyword coverage
                              decides whether the question is in scope at all.
  4. Planning + tool calls  – either the LLM plans with function calling ("llm" mode), or the
                              deterministic workflow planner does ("rules" mode, no API key needed).
                              Every tool call goes through the MCP client; nothing is called directly.
  5. Citation verification  – every [POL-XXX §N] in the answer must exist in the policy library;
                              unknown sections are fetched with `get_policy_section` or removed.
  6. Response               – answer, citations with snippets, trace, pending action, escalation flag.

The trace is an *operational* log (tools, arguments, outputs, sources, decisions), not hidden
chain-of-thought.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import Settings, get_settings
from . import guardrails, nlu
from .llm import LLMClient, LLMUnavailable
from .mcp_client import MCPToolClient, ToolCallResult
from .prompts import CONTEXT_TEMPLATE, SYSTEM_PROMPT

log = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\[?\b(POL-\d{3})\s*§\s*(\d+(?:\.\d+)*)\]?")


# ---------------------------------------------------------------------------
# Data structures returned to the web app
# ---------------------------------------------------------------------------
@dataclass
class TraceStep:
    step: int
    kind: str  # guardrail | retrieval | plan | tool_call | llm | citation_check | escalation | action | final
    name: str
    status: str = "ok"  # ok | error | blocked | info
    arguments: dict | None = None
    output: Any = None
    duration_ms: float | None = None


@dataclass
class ChatResult:
    answer: str
    citations: list[dict]
    trace: list[dict]
    mode: str
    intent: str | None = None
    employee_id: str | None = None
    session_id: str | None = None
    pending_action: dict | None = None
    escalation: dict | None = None
    clarification_needed: bool = False
    blocked_reason: str | None = None
    tools_called: list[str] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Session:
    history: list[dict] = field(default_factory=list)  # last few {"role", "content"} turns, for the LLM
    pending: dict[str, dict] = field(default_factory=dict)  # action_id -> pending mock action


class Evidence:
    """Everything the agent learned during one request, used for citations and grounding."""

    def __init__(self) -> None:
        self.sections: dict[str, dict] = {}  # "POL-002 §5" -> {citation, doc_id, title, section, snippet}
        self.ranked: list[str] = []  # citations in retrieval order (best first)
        self.tool_citations: set[str] = set()  # citations asserted by rule-engine tools
        self.keyword_coverage: float | None = None
        self.unmatched_terms: list[str] = []  # question words that appear nowhere in the library

    def add_search(self, output: dict) -> None:
        if self.keyword_coverage is None and "keyword_coverage" in output:
            self.keyword_coverage = output["keyword_coverage"]
            self.unmatched_terms = output.get("unmatched_terms", [])
        for r in output.get("results", []):
            self._add(r["citation"], r["doc_id"], r["title"], r["section"], r["snippet"], r.get("score"))

    def add_section(self, output: dict) -> None:
        if "citation" in output:
            self._add(output["citation"], output["doc_id"], output["title"], output["section"], output["text"][:1200], None)

    def _add(self, citation, doc_id, title, section, snippet, score) -> None:
        if citation not in self.sections:
            self.sections[citation] = {"citation": citation, "doc_id": doc_id, "title": title, "section": section,
                                       "snippet": snippet, "score": score}
            self.ranked.append(citation)

    def add_tool_citations(self, output: dict) -> None:
        for c in output.get("citations", []) or []:
            self.tool_citations.add(c)
        for check in output.get("checks", []) or []:
            if check.get("citation"):
                self.tool_citations.add(check["citation"])

    def top(self, n: int) -> list[dict]:
        return [self.sections[c] for c in self.ranked[:n]]


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
class HRAgent:
    def __init__(self, client: MCPToolClient, settings: Settings | None = None):
        self.client = client
        self.settings = settings or get_settings()
        self.sessions: dict[str, Session] = {}
        self._llm: LLMClient | None = None
        self.llm_error: str | None = None
        if self.settings.use_llm:
            try:
                self._llm = LLMClient(self.settings)
            except LLMUnavailable as exc:
                self.llm_error = str(exc)

    @property
    def mode(self) -> str:
        return "llm" if self._llm else "rules"

    # ------------------------------------------------------------------ public entry point
    async def chat(
        self,
        message: str,
        employee_id: str | None = None,
        session_id: str | None = None,
        confirm_action_id: str | None = None,
        cancel_action_id: str | None = None,
        mode: str | None = None,
    ) -> ChatResult:
        started = time.perf_counter()
        session_id = session_id or uuid.uuid4().hex[:12]
        session = self.sessions.setdefault(session_id, Session())
        run = _Run(self, session, session_id, employee_id, mode or self.mode)
        try:
            result = await run.execute(message or "", confirm_action_id, cancel_action_id)
        except Exception as exc:  # last-resort safety net: never return a 500 to the chat UI
            log.exception("Agent failure")
            run.add("final", "unexpected_error", status="error", output=str(exc))
            result = run.finish("Sorry, something went wrong while handling that request. Please try again, "
                                "or open an HR ticket if it keeps happening.")
        result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        session.history = (session.history + [{"role": "user", "content": message},
                                              {"role": "assistant", "content": result.answer}])[-8:]
        return result


class _Run:
    """State for a single request (kept separate from the long-lived HRAgent)."""

    def __init__(self, agent: HRAgent, session: Session, session_id: str, employee_id: str | None, mode: str):
        self.agent = agent
        self.client = agent.client
        self.settings = agent.settings
        self.session = session
        self.session_id = session_id
        self.acting_employee_id = employee_id.strip().upper() if employee_id else None
        self.employee_id: str | None = self.acting_employee_id
        self.mode = mode if (mode != "llm" or agent._llm) else "rules"
        self.trace: list[TraceStep] = []
        self.evidence = Evidence()
        self.tools_called: list[str] = []
        self.pending_action: dict | None = None
        self.escalation: dict | None = None
        self.clarification = False
        self.blocked_reason: str | None = None
        self.intent: str | None = None
        self.today = self.settings.today()

    # ------------------------------------------------------------------ helpers
    def add(self, kind: str, name: str, status: str = "ok", arguments: dict | None = None,
            output: Any = None, duration_ms: float | None = None) -> None:
        self.trace.append(TraceStep(len(self.trace) + 1, kind, name, status, arguments, output, duration_ms))

    async def call(self, name: str, arguments: dict) -> ToolCallResult:
        """Call an MCP tool with the agent-side safety rules applied, and record it in the trace."""
        arguments = dict(arguments)
        # Privacy: tools may only be called for the acting employee's own records.
        if "employee_id" in arguments:
            if self.employee_id and arguments.get("employee_id", "").upper() != self.employee_id:
                self.add("guardrail", "employee_id_override", status="blocked",
                         output=f"Tool asked for {arguments.get('employee_id')}; restricted to {self.employee_id}.")
                arguments["employee_id"] = self.employee_id
        # Action safety: the agent can never confirm a ticket by itself.
        if name == "create_mock_hr_ticket" and arguments.get("confirm"):
            self.add("guardrail", "confirmation_required", status="blocked",
                     output="confirm=true removed; tickets need explicit user confirmation in the app.")
            arguments["confirm"] = False

        result = await self.client.call(name, arguments)
        self.tools_called.append(name)
        self.add("tool_call", name, "ok" if result.ok else "error", arguments, _summarize(name, result.output), result.duration_ms)

        out = result.output
        if result.ok:
            if name == "search_policy_documents":
                self.evidence.add_search(out)
            elif name == "get_policy_section":
                self.evidence.add_section(out)
            else:
                self.evidence.add_tool_citations(out)
            if name == "create_mock_hr_ticket" and out.get("status") == "preview":
                self._register_pending(out, arguments)
        return result

    def _register_pending(self, out: dict, arguments: dict) -> None:
        action_id = uuid.uuid4().hex[:10]
        preview = out["ticket_preview"]
        self.session.pending[action_id] = {"tool": "create_mock_hr_ticket", "arguments": {**arguments, "confirm": True},
                                           "preview": preview}
        self.pending_action = {
            "action_id": action_id,
            "type": "create_mock_hr_ticket",
            "description": f"Create a mock {preview['category']} ticket ({preview['priority']}): {preview['summary']}",
            "preview": preview,
        }

    def finish(self, answer: str) -> ChatResult:
        citations = self._final_citations(answer)
        self.add("final", "response", output={
            "mode": self.mode,
            "intent": self.intent,
            "answer_basis": {
                "tools": sorted(set(self.tools_called)),
                "cited_sources": [c["citation"] for c in citations],
                "decision_tools": [s.output.get("decision") for s in self.trace
                                   if s.name == "check_policy_compliance" and isinstance(s.output, dict) and s.output.get("decision")],
            },
            "escalation": bool(self.escalation),
            "pending_action": self.pending_action["action_id"] if self.pending_action else None,
        })
        return ChatResult(
            answer=answer.strip(),
            citations=citations,
            trace=[asdict(s) for s in self.trace],
            mode=self.mode,
            intent=self.intent,
            employee_id=self.employee_id,
            session_id=self.session_id,
            pending_action=self.pending_action,
            escalation=self.escalation,
            clarification_needed=self.clarification,
            blocked_reason=self.blocked_reason,
            tools_called=self.tools_called,
        )

    def _final_citations(self, answer: str) -> list[dict]:
        cited = [f"{m.group(1)} §{m.group(2)}" for m in CITATION_RE.finditer(answer)]
        out, seen = [], set()
        for c in cited:
            if c in seen:
                continue
            seen.add(c)
            if c in self.evidence.sections:
                out.append(self.evidence.sections[c])
        return out

    # ------------------------------------------------------------------ main flow
    async def execute(self, message: str, confirm_id: str | None, cancel_id: str | None) -> ChatResult:
        # 1. Confirmation of a pending mock action (button in the UI, or "yes" in chat).
        handled = await self._handle_confirmation(message, confirm_id, cancel_id)
        if handled:
            return handled

        # 2. Pre-guardrails.
        guard = guardrails.pre_check(message, self.acting_employee_id)
        self.employee_id = guard.employee_id
        self.add("guardrail", "pre_check", status="blocked" if guard.blocked else "ok", output={
            "employee_id": guard.employee_id, "hr_topic": guard.hr_topic, "sensitive": guard.sensitive_category,
            "injection_detected": guard.injection_detected, "blocked": guard.block_reason})
        if guard.blocked:
            self.blocked_reason = guard.block_reason
            if guard.block_reason == "emergency":
                self.escalation = {"reason": "possible_emergency", "route": "emergency services / Employee Relations P1"}
            return self.finish(guard.message)
        if not message.strip():
            self.clarification = True
            return self.finish("Please type your HR question.")

        # 3. Initial evidence + scope check.
        ex = nlu.extract(message, self.today)
        self.intent = "hr_case_triage" if guard.sensitive_category else ex.intent
        search = await self.call("search_policy_documents", {"query": message, "k": self.settings.retrieval_k})
        coverage = search.output.get("keyword_coverage", 0.0) if search.ok else 1.0
        # Sensitive workplace concerns are always in scope, whatever words the person uses.
        in_hr = guard.hr_topic or bool(guard.sensitive_category)
        unmatched = search.output.get("unmatched_terms", []) if search.ok else []
        if guardrails.is_out_of_scope(in_hr, coverage, unmatched) or (guard.injection_detected and not in_hr):
            self.blocked_reason = "out_of_scope"
            self.intent = "out_of_scope"
            self.add("guardrail", "scope_check", status="blocked",
                     output={"keyword_coverage": coverage, "unmatched_terms": unmatched, "hr_topic": guard.hr_topic})
            return self.finish(guardrails.OUT_OF_SCOPE_MESSAGE)
        self.add("guardrail", "scope_check", output={"keyword_coverage": coverage, "in_scope": True,
                                                     "evidence_available": search.ok})
        if in_hr and (guard.injection_detected or guard.approval_request):
            # Attempts to override the rules or to have the assistant approve something are answered
            # deterministically (in both planner modes) so a model can never be talked into "approving".
            self.intent = "unsafe_request"
            self.add("guardrail", "unsafe_request", status="blocked",
                     output={"injection_detected": guard.injection_detected, "approval_request": guard.approval_request})
            return self.finish(await self._verify_citations(guardrails.UNSAFE_REQUEST_MESSAGE))
        if guard.sensitive_category:
            self.escalation = {"reason": guard.sensitive_category, "route": "Employee Relations (P1, restricted)",
                               "policy": ["POL-012 §4", "POL-013 §5", "POL-013 §6"]}
            self.add("escalation", "sensitive_topic", status="info", output=self.escalation)
        self.add("plan", "intent", output={"intent": self.intent, "mode": self.mode,
                                           "extracted": _extracted_summary(ex)})

        # 4. Plan and act.
        answer: str | None = None
        if self.mode == "llm":
            try:
                answer = await self._run_llm(message, ex, guard)
            except LLMUnavailable as exc:
                self.add("llm", "llm_unavailable", status="error", output=f"{exc}; falling back to the rules planner.")
                self.mode = "rules"
        if answer is None:
            answer = await self._run_rules(message, ex, guard)

        # 5. Citation verification.
        answer = await self._verify_citations(answer)
        if guard.needs_advice_disclaimer and "not personal" not in answer:
            answer += ("\n\n_Note: this is policy information, not personal tax, legal, immigration or medical advice "
                       "(POL-013 §6)._")
            answer = await self._verify_citations(answer)
        return self.finish(answer)

    # ------------------------------------------------------------------ confirmation
    async def _handle_confirmation(self, message: str, confirm_id: str | None, cancel_id: str | None) -> ChatResult | None:
        pending = self.session.pending
        if not pending:
            if confirm_id:
                self.intent = "confirm_action"
                self.add("action", "confirm", status="error", output="No pending action with that ID (it may have expired).")
                return self.finish("There is no pending action to confirm. Ask me again and I'll prepare a new preview.")
            return None
        latest_id = list(pending)[-1]
        if cancel_id or (not confirm_id and guardrails.CANCEL_RE.match(message or "")):
            action_id = cancel_id or latest_id
            pending.pop(action_id, None)
            self.intent = "cancel_action"
            self.add("action", "cancel", output={"action_id": action_id})
            return self.finish("Okay, I cancelled that. No ticket was created.")
        if not confirm_id and guardrails.CONFIRM_RE.match(message or ""):
            confirm_id = latest_id
        if not confirm_id:
            return None
        action = pending.pop(confirm_id, None)
        self.intent = "confirm_action"
        if not action:
            self.add("action", "confirm", status="error", output="Unknown or expired action ID.")
            return self.finish("That action has expired or was already handled. Nothing was created.")
        self.employee_id = action["arguments"].get("employee_id")
        self.add("action", "user_confirmed", output={"action_id": confirm_id, "tool": action["tool"]})
        result = await self.client.call(action["tool"], action["arguments"])  # confirm=True is allowed only here
        self.tools_called.append(action["tool"])
        self.add("tool_call", action["tool"], "ok" if result.ok else "error", action["arguments"],
                 _summarize(action["tool"], result.output), result.duration_ms)
        if not result.ok:
            return self.finish("I couldn't create the mock ticket because the HR tools are unavailable right now. "
                               "Nothing was created; please try again shortly.")
        t = result.output["ticket"]
        sla = result.output.get("expected_first_response")
        answer = (f"**Done.** Mock ticket **{t['ticket_id']}** was created in the *{t['category']}* queue with priority "
                  f"**{t['priority']}**. Expected first response: {sla} [POL-013 §4].\n\n"
                  "_This is a simulated ticket for the course demo; no real HR system was contacted._")
        return self.finish(await self._verify_citations(answer))

    # ------------------------------------------------------------------ citation verification
    async def _verify_citations(self, answer: str) -> str:
        cited = []
        for m in CITATION_RE.finditer(answer):
            key = f"{m.group(1)} §{m.group(2)}"
            if key not in cited:
                cited.append(key)
        missing = [c for c in cited if c not in self.evidence.sections]
        invalid = []
        # Fetch the cited sections we have not seen yet, in parallel (each is one MCP get_policy_section call).
        fetched = await asyncio.gather(*[
            self.call("get_policy_section", {"doc_id": c.split(" §")[0], "section": c.split(" §")[1]}) for c in missing[:8]
        ])
        for c, res in zip(missing[:8], fetched):
            if not res.ok:
                invalid.append(c)
        for c in missing[8:]:
            invalid.append(c)
        for c in invalid:
            doc_id, sec = c.split(" §")
            answer = re.sub(rf"\[?{re.escape(doc_id)}\s*§\s*{re.escape(sec)}\]?", "", answer)
        self.add("citation_check", "verify_citations", status="ok" if not invalid else "error",
                 output={"cited": cited, "fetched_to_verify": [c for c in missing if c not in invalid], "removed_invalid": invalid})
        return answer

    # ------------------------------------------------------------------ LLM planner
    async def _run_llm(self, message: str, ex: nlu.Extracted, guard: guardrails.GuardrailResult) -> str:
        llm = self.agent._llm
        evidence = "\n".join(
            f"- [{e['citation']}] {e['title']} > {e['section']}: {e['snippet'][:420]}" for e in self.evidence.top(5)
        ) or "(none)"
        date_hints = "; ".join(ex.notes) or (
            f"start={ex.start_date.isoformat()}" if ex.start_date else "none")
        context = CONTEXT_TEMPLATE.format(
            today=self.today.isoformat(), weekday=self.today.strftime("%A"),
            next_monday=nlu.next_monday(self.today).isoformat(),
            employee_id=self.employee_id or "UNKNOWN (ask the user for their employee ID before using employee tools)",
            date_hints=date_hints, sensitive=guard.sensitive_category or "no",
            unmatched=", ".join(self.evidence.unmatched_terms) or "none",
            evidence=evidence,
        )
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + context}]
        messages += self.session.history[-4:]
        messages.append({"role": "user", "content": message})
        tools = self.client.openai_tools()

        for step in range(self.settings.max_agent_steps):
            reply = await llm.complete(messages, tools=tools)
            self.add("llm", f"llm_turn_{step + 1}", output={
                "tool_calls_requested": [tc["name"] for tc in reply["tool_calls"]],
                "tokens": reply["usage"]}, duration_ms=reply["latency_ms"])
            if not reply["tool_calls"]:
                content = reply["content"]
                # The model asked the user for missing details (its reply ends with a question).
                last_line = next((ln for ln in reversed(content.strip().splitlines()) if ln.strip()), "")
                if last_line.rstrip(" *_").endswith("?"):
                    self.clarification = True
                return content
            messages.append({"role": "assistant", "content": reply["content"] or None, "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in reply["tool_calls"]]})
            for tc in reply["tool_calls"]:
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if self.employee_id and tc["name"] in _EMPLOYEE_TOOLS:
                    args.setdefault("employee_id", self.employee_id)
                result = await self.call(tc["name"], _drop_nulls(args))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _compact_for_llm(tc["name"], result.output)})
        # Out of steps: ask for a final answer without tools.
        messages.append({"role": "user", "content": "Give your final answer now using only the tool results above."})
        reply = await llm.complete(messages, tools=None)
        self.add("llm", "llm_final", output={"tokens": reply["usage"]}, duration_ms=reply["latency_ms"])
        return reply["content"]

    # ------------------------------------------------------------------ rules planner
    async def _run_rules(self, message: str, ex: nlu.Extracted, guard: guardrails.GuardrailResult) -> str:
        intent = self.intent
        needs_employee = intent in ("pto_request", "pto_balance", "work_from_abroad", "remote_work_domestic",
                                    "expense_check", "benefits_triage")
        if needs_employee and not self.employee_id and intent in ("benefits_triage", "expense_check"):
            # General eligibility questions can be answered from policy alone; personal checks need an ID.
            self.add("plan", "policy_only", status="info",
                     output="No employee ID: answering from policy only (no personal records checked).")
            answer = await self._wf_policy_question(message, ex)
            return answer + "\n\n_Select your employee ID in **Acting as** if you want me to check your own records._"
        if needs_employee and not self.employee_id:
            self.clarification = True
            self.add("plan", "clarification", status="info", output="Employee ID required for this workflow.")
            facts = self._facts_from_evidence(message, 2)
            return ("To check this against **your** records I need your employee ID (for example E1003). Choose it in "
                    "**Acting as**, or include it in your message.\n\n" + (f"**Policy facts:**\n{facts}" if facts else ""))
        workflow = {
            "pto_request": self._wf_pto, "pto_balance": self._wf_pto_balance, "work_from_abroad": self._wf_abroad,
            "remote_work_domestic": self._wf_remote_domestic, "expense_check": self._wf_expense,
            "benefits_triage": self._wf_benefits, "hr_case_triage": self._wf_case,
        }.get(intent, self._wf_policy_question)
        self.add("plan", "workflow", output={"workflow": workflow.__name__.replace("_wf_", "")})
        return await workflow(message, ex)

    # ---- workflow: PTO request -------------------------------------------------
    async def _wf_pto(self, message: str, ex: nlu.Extracted) -> str:
        profile = await self.call("lookup_employee_profile", {"employee_id": self.employee_id})
        if not profile.ok:
            return self._tool_failure_answer(profile)
        balance = await self.call("check_pto_balance", {"employee_id": self.employee_id})
        await self.call("search_policy_documents", {"query": "PTO notice requirements manager approval request",
                                                    "k": 4, "doc_ids": ["POL-002"]})
        days = ex.workdays or ex.days or (ex.weeks * 5 if ex.weeks else None)
        if not days and not ex.start_date:
            self.clarification = True
            b = balance.output
            return (f"You currently have **{b.get('available_after_pending_days')} days** of PTO available after pending "
                    f"requests [POL-002 §3]. How many days do you want to take, and starting when? I'll then check notice "
                    f"and approval rules [POL-002 §5].")
        args = {"employee_id": self.employee_id, "request_type": "pto", "days": days,
                "start_date": ex.start_date.isoformat() if ex.start_date else None,
                "end_date": ex.end_date.isoformat() if ex.end_date else None}
        comp = await self.call("check_policy_compliance", _drop_nulls(args))
        if not comp.ok:
            return self._tool_failure_answer(comp, partial=balance.output)
        c = comp.output
        email = None
        if c["decision"] in ("compliant", "needs_approval"):
            mgr = (profile.output.get("manager") or {}).get("name", "your manager")
            points = [f"Dates: {c.get('start_date', 'TBD')} to {c.get('end_date', 'TBD')} ({c.get('requested_days'):g} business days)",
                      f"Remaining PTO after this request: {balance.output['available_after_pending_days'] - c['requested_days']:g} days",
                      "I will arrange coverage and set an out-of-office message"]
            email = await self.call("draft_hr_email", {"employee_id": self.employee_id, "recipient": "manager",
                                                       "purpose": "pto_request", "key_points": points})
            if email.ok:
                email.output["to_name"] = mgr
        return self._compose_decision_answer("PTO request", c, balance=balance.output, email=email, notes=ex.notes)

    async def _wf_pto_balance(self, message: str, ex: nlu.Extracted) -> str:
        bal = await self.call("check_pto_balance", {"employee_id": self.employee_id})
        if not bal.ok:
            return self._tool_failure_answer(bal)
        await self.call("search_policy_documents", {"query": "PTO balance cap carryover accrual", "k": 3, "doc_ids": ["POL-002"]})
        b = bal.output
        if not b.get("eligible"):
            return f"**Answer:** {b.get('reason')}"
        return (f"**Answer:** You have **{b['available_days']} days** of PTO available "
                f"({b['available_after_pending_days']} after pending requests) as of {b['as_of']}.\n\n"
                f"**Your data:**\n- Annual entitlement: {b['annual_entitlement_days']} days (tenure {b['tenure_years']} years) [POL-002 §3]\n"
                f"- Carried over: {b['carryover_days']}; accrued this year: {b['accrued_ytd_days']}; used: {b['used_ytd_days']}\n"
                f"- Floating holidays left: {b['floating_holidays_remaining']}; sick days left: {b['sick_days_remaining']}\n\n"
                f"**Policy facts:**\n- The balance cap is {b['balance_cap_days']} days; accrual pauses at the cap [POL-002 §3.1].\n"
                f"- Up to 5 days carry over into next year; the rest is forfeited on December 31 [POL-002 §3.2].")

    # ---- workflow: work from abroad -------------------------------------------------
    async def _wf_abroad(self, message: str, ex: nlu.Extracted) -> str:
        profile = await self.call("lookup_employee_profile", {"employee_id": self.employee_id})
        if not profile.ok:
            return self._tool_failure_answer(profile)
        await self.call("search_policy_documents", {"query": "work from abroad duration limit workdays approvals",
                                                    "k": 4, "doc_ids": ["POL-005"]})
        await self.call("search_policy_documents", {"query": "security requirements working abroad VPN device restricted data",
                                                    "k": 3, "doc_ids": ["POL-005", "POL-006"]})
        if not (ex.weeks or ex.workdays or ex.days or (ex.start_date and ex.end_date)):
            self.clarification = True
            return (f"Working from {ex.country or 'another country'} is limited to 20 workdays in any rolling 12 months "
                    "and needs manager, Global Mobility and Information Security approval [POL-005 §4]. How long do you plan "
                    "to work from there (dates or number of weeks)?")
        args = {"employee_id": self.employee_id, "request_type": "work_from_abroad", "destination_country": ex.country,
                "weeks": ex.weeks, "workdays": ex.workdays or (int(ex.days) if ex.days and not ex.weeks else None),
                "start_date": ex.start_date.isoformat() if ex.start_date else None,
                "end_date": ex.end_date.isoformat() if ex.end_date else None}
        comp = await self.call("check_policy_compliance", _drop_nulls(args))
        if not comp.ok:
            return self._tool_failure_answer(comp, partial=profile.output)
        c = comp.output
        ticket = None
        exceeds = any(ch["rule"].startswith("Within 20 workdays") and ch["passed"] is False for ch in c["checks"])
        if exceeds:
            ticket = await self.call("create_mock_hr_ticket", {
                "employee_id": self.employee_id, "category": "Global Mobility",
                "summary": f"Global Mobility review: work from {ex.country} for about {c.get('workdays_requested')} workdays",
                "details": "Exceeds the 20-workday WFA limit (POL-005 §4); requesting a Global Mobility review."})
        return self._compose_decision_answer(f"Working from {ex.country}", c, profile=profile.output, ticket=ticket, notes=ex.notes)

    async def _wf_remote_domestic(self, message: str, ex: nlu.Extracted) -> str:
        profile = await self.call("lookup_employee_profile", {"employee_id": self.employee_id})
        if not profile.ok:
            return self._tool_failure_answer(profile)
        await self.call("search_policy_documents", {"query": "temporary work from another state calendar days approval payroll",
                                                    "k": 4, "doc_ids": ["POL-004"]})
        cal = int(round(ex.weeks * 7)) if ex.weeks else (int(ex.days) if ex.days else None)
        if not cal and not (ex.start_date and ex.end_date):
            self.clarification = True
            return ("Temporary work from another state or province is allowed in tiers by total calendar days per year "
                    "[POL-004 §4]. How many days do you plan to work from there?")
        args = {"employee_id": self.employee_id, "request_type": "remote_work_domestic", "destination_state": ex.state,
                "calendar_days": cal, "start_date": ex.start_date.isoformat() if ex.start_date else None,
                "end_date": ex.end_date.isoformat() if ex.end_date else None}
        comp = await self.call("check_policy_compliance", _drop_nulls(args))
        if not comp.ok:
            return self._tool_failure_answer(comp)
        return self._compose_decision_answer("Temporary remote work", comp.output, profile=profile.output, notes=ex.notes)

    # ---- workflow: expenses -------------------------------------------------
    async def _wf_expense(self, message: str, ex: nlu.Extracted) -> str:
        profile = await self.call("lookup_employee_profile", {"employee_id": self.employee_id})
        if not profile.ok:
            return self._tool_failure_answer(profile)
        item = ex.expense_item or "expense"
        await self.call("search_policy_documents", {"query": f"{item} reimbursement home office stipend non-reimbursable",
                                                    "k": 4, "doc_ids": ["POL-007", "POL-011"]})
        comp = await self.call("check_policy_compliance", _drop_nulls({
            "employee_id": self.employee_id, "request_type": "expense", "expense_category": item, "amount": ex.amount,
            "travel_type": "international" if re.search(r"\binternational\b", message.lower()) else None}))
        if not comp.ok:
            return self._tool_failure_answer(comp)
        return self._compose_decision_answer(f"Reimbursing a {item}", comp.output, profile=profile.output)

    # ---- workflow: benefits -------------------------------------------------
    async def _wf_benefits(self, message: str, ex: nlu.Extracted) -> str:
        status = await self.call("lookup_benefits_status", {"employee_id": self.employee_id})
        if not status.ok:
            return self._tool_failure_answer(status)
        await self.call("search_policy_documents", {"query": message, "k": 4, "doc_ids": ["POL-008", "POL-001"]})
        s = status.output
        elig = s["eligibility"]
        eligible = [k.replace("_", " ") for k, v in elig.items() if v]
        not_eligible = [k.replace("_", " ") for k, v in elig.items() if not v]
        elections = s.get("elections") or {}
        med = (elections.get("medical") or {})
        lines = [f"**Answer:** As a {s['employment_type'].replace('_', '-')} employee (FTE {s['fte']}), you are eligible for: "
                 f"{', '.join(eligible)} [POL-001 §4]."]
        if not_eligible:
            lines.append(f"Not eligible: {', '.join(not_eligible)} [POL-008 §2].")
        lines.append("\n**Your data:**")
        lines.append(f"- Enrollment status: {elections.get('enrollment_status', 'unknown').replace('_', ' ')}"
                     + (f" (deadline {elections['enrollment_deadline']})" if elections.get("enrollment_deadline") else ""))
        if med:
            lines.append(f"- Medical: {med.get('plan')} ({med.get('tier', '').replace('_', ' ')})")
        if elections.get("retirement"):
            lines.append(f"- Retirement: {elections['retirement']['plan']} at {elections['retirement']['contribution_pct']}%")
        facts = self._facts_from_evidence(message, 3)
        if facts:
            lines.append("\n**Policy facts:**\n" + facts)
        lines.append("\n**Recommended next steps:**")
        if elections.get("enrollment_status") == "enrollment_window_open":
            lines.append("- Enroll in PeopleHub before your deadline; otherwise you are auto-enrolled in employee-only Basic PPO "
                         "with no dental or vision [POL-008 §3].")
        lines.append("- Changes outside open enrollment need a qualifying life event within 30 days [POL-008 §8].")
        lines.append("- For questions not answered here, open a Benefits ticket (Total Rewards replies within 3 business days) [POL-008 §10].")
        return "\n".join(lines)

    # ---- workflow: sensitive case triage ------------------------------------------------
    async def _wf_case(self, message: str, ex: nlu.Extracted) -> str:
        await self.call("get_policy_section", {"doc_id": "POL-012", "section": "4"})
        await self.call("get_policy_section", {"doc_id": "POL-013", "section": "6"})
        lines = [
            "**Answer:** I'm sorry you're dealing with this. This needs to go to a trained person in Employee Relations; "
            "I can't investigate or assess what happened, but I can help you report it confidentially.",
            "\n**Policy facts:**",
            "- You can report to any manager you trust, to People Operations via an Employee Relations ticket (visible only "
            "to that team), or the 24/7 Ethics Hotline, which accepts anonymous reports where law allows [POL-012 §4].",
            "- Employee Relations acknowledges within 1 business day and an investigation starts within 5 business days [POL-012 §5].",
            "- Retaliation for a good-faith report is prohibited [POL-012 §3.6].",
            "- Free, confidential counseling is available through the EAP [POL-012 §8].",
            "- If anyone is in immediate danger, contact emergency services first [POL-012 §3.5].",
        ]
        if self.employee_id:
            await self.call("create_mock_hr_ticket", {
                "employee_id": self.employee_id, "category": "Employee Relations",
                "summary": "Employee wants to report a workplace conduct concern (details to be shared with Employee Relations).",
                "details": "Raised via HR Assistant. Details intentionally not recorded by the assistant."})
            lines.append("\n**Recommended next step:** I've prepared a confidential **Employee Relations** ticket (priority P1). "
                         "It will only be created if you confirm below [POL-013 §4].")
        else:
            self.clarification = True
            lines.append("\n**Recommended next step:** If you'd like me to prepare a confidential Employee Relations ticket, "
                         "select your employee ID in **Acting as**. You can also use the Ethics Hotline anonymously [POL-012 §4].")
        return "\n".join(lines)

    # ---- workflow: plain policy question ------------------------------------------------
    async def _wf_policy_question(self, message: str, ex: nlu.Extracted) -> str:
        top = self.evidence.top(5)
        best = top[0]["score"] if top and top[0].get("score") is not None else 0
        if not top or best < 0.12:
            self.escalation = {"reason": "insufficient_policy_evidence", "route": "HR ticket"}
            return ("**Answer:** I couldn't find this in the Lumora policy library, so I won't guess. "
                    "Please open an HR ticket so People Operations can answer it [POL-013 §5].")
        facts = self._facts_from_evidence(message, 4)
        if self.evidence.unmatched_terms and (self.evidence.keyword_coverage or 0) < 0.6:
            # The question uses words the library never mentions: say so instead of stretching related text.
            terms = ", ".join(f"'{t}'" for t in self.evidence.unmatched_terms)
            self.escalation = {"reason": "not_covered_by_policy", "route": "HR ticket", "unmatched_terms": self.evidence.unmatched_terms}
            return (f"**Answer:** The Lumora policy library doesn't mention {terms}, so I can't confirm this from policy. "
                    "Please open an HR ticket and People Operations can answer it [POL-013 §5].\n\n"
                    f"**Related policy facts (may not answer your question):**\n{facts}")
        docs = {e["doc_id"] for e in top[:3]}
        return (f"**Answer:** Here is what the policy library says"
                f"{' (from ' + str(len(docs)) + ' policies)' if len(docs) > 1 else ''}:\n\n**Policy facts:**\n{facts}\n\n"
                "**Recommended next step:** if your situation isn't covered above, open an HR ticket so People Operations can confirm.")

    # ------------------------------------------------------------------ answer composition (rules mode)
    def _facts_from_evidence(self, query: str, n: int) -> str:
        """Pick the sentences from retrieved chunks that best match the question (extractive answer)."""
        q_tokens = set(re.findall(r"[a-z0-9]+", nlu_rewrite(query).lower())) - _STOP
        candidates = []
        for rank, e in enumerate(self.evidence.top(6)):
            for sent in re.split(r"(?<=[.!?])\s+|\n+", e["snippet"]):
                sent = sent.strip(" -")
                if len(sent.split()) < 5 or ("|" in sent and not re.search(r"\d", sent)) or sent[:1].islower():
                    continue  # too short, a table header row, or a list fragment without its lead-in sentence
                candidates.append((rank, e["citation"], sent, set(re.findall(r"[a-z0-9]+", sent.lower()))))
        # Rare words (e.g. "grandparent") count more than words found in many sentences (e.g. "leave").
        df: dict[str, int] = {}
        for *_, toks in candidates:
            for t in toks & q_tokens:
                df[t] = df.get(t, 0) + 1
        scored = []
        for rank, cit, sent, toks in candidates:
            weight = sum(1.0 / df[t] for t in toks & q_tokens)
            if weight:
                # Prefer higher-ranked chunks and sentences that state a rule rather than point to another policy.
                scored.append((weight - rank * 0.15 - (0.3 if "(POL-" in sent else 0), cit, sent))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked, seen = [], set()
        # "How many / how much / how long" questions are usually answered by a table (accrual tiers, notice
        # periods, caps). Show the most relevant table from the top chunks, row by row, as one cited fact.
        if re.search(r"\b(how (many|much|long|soon|quickly)|what is the (limit|cap|match|amount)|when (are|is))\b", query.lower()):
            for e in self.evidence.top(3):
                rows = [r.strip() for r in e["snippet"].splitlines() if "|" in r and re.search(r"\d", r)]
                if len(rows) >= 2:
                    picked.append(f"- Table from {e['section']}: " + "; ".join(r.replace(" | ", ": ", 1) for r in rows[:6]) + f". [{e['citation']}]")
                    break
        # At most half of the facts may come from one document, so questions that span several
        # policies get evidence from each of them.
        per_doc: dict[str, int] = {}
        cap = max(2, (n + 1) // 2)
        for _, cit, sent in scored:
            doc = cit.split(" §")[0]
            if sent in seen or per_doc.get(doc, 0) >= cap:
                continue
            seen.add(sent)
            per_doc[doc] = per_doc.get(doc, 0) + 1
            picked.append(f"- {sent.rstrip('.')}. [{cit}]")
            if len(picked) >= n:
                break
        return "\n".join(picked)

    def _compose_decision_answer(self, topic: str, c: dict, balance: dict | None = None, profile: dict | None = None,
                                 email: ToolCallResult | None = None, ticket: ToolCallResult | None = None,
                                 notes: list[str] | None = None) -> str:
        decision = c["decision"]
        verdict = {
            "compliant": "Yes, this meets the policy rules.",
            "needs_approval": "Yes, this is possible, subject to the approvals below.",
            "partially_compliant": "Partly: only part of this is covered by policy.",
            "not_compliant": "Not as requested: it does not meet the policy rules below.",
            "not_eligible": "No, you are not eligible for this.",
            "needs_more_info": "I need a bit more information to check this fully.",
        }[decision]
        if c.get("decision_note"):
            verdict += f" {c['decision_note']}"
        lines = [f"**Answer:** {topic}: {verdict}"]
        if notes:
            lines.append("_" + " ".join(notes) + "_")
        lines.append("\n**Policy checks:**")
        for ch in c["checks"]:
            icon = "✅" if ch["passed"] else ("❌" if ch["passed"] is False else "❔")
            detail = re.sub(r"\s*\((POL-\d{3}[^)]*)\)", "", ch["detail"])  # citation is added once, below
            lines.append(f"- {icon} {ch['rule']}: {detail} [{ch['citation']}]")
        if balance and balance.get("eligible"):
            lines.append("\n**Your data:**")
            lines.append(f"- PTO available: {balance['available_days']} days; after pending requests: "
                         f"{balance['available_after_pending_days']} days (as of {balance['as_of']}) [POL-002 §3]")
        if c.get("required_approvals"):
            lines.append("\n**Approvals required:** " + "; ".join(c["required_approvals"]))
        lines.append("\n**Recommended next steps:**")
        for step in c.get("next_steps", [])[:8]:
            lines.append("- " + _bracket_citations(step))
        if c.get("request_type") == "pto":
            lines.append("\n_No PTO request has been submitted. Only you can submit it in PeopleHub; any mock ticket "
                         "would need your explicit confirmation first._")
        if email is not None and email.ok:
            lines.append(f"\n**Draft message to {email.output.get('to_name', email.output['to'])}** (not sent, review it first):\n")
            lines.append(f"> **Subject:** {email.output['subject']}\n>\n" +
                         "\n".join("> " + ln for ln in email.output["body"].splitlines()))
        if ticket is not None and ticket.ok:
            lines.append("\nI've prepared a **Global Mobility review** ticket. It will only be created if you confirm below "
                         "[POL-013 §6].")
        return "\n".join(lines)

    def _tool_failure_answer(self, failed: ToolCallResult, partial: dict | None = None) -> str:
        err = failed.output.get("error")
        if err in ("employee_not_found", "missing_employee_id"):
            self.clarification = True
            return f"{failed.output.get('message')} Please check your employee ID (for example E1003) and try again."
        facts = self._facts_from_evidence(failed.name.replace("_", " "), 2)
        return (f"I couldn't complete this check because the `{failed.name}` tool is unavailable right now "
                f"({err}). I haven't guessed the result. " + (f"\n\n**What the policy says:**\n{facts}" if facts else "")
                + "\n\nPlease try again in a moment, or open an HR ticket.")


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------
_EMPLOYEE_TOOLS = {"lookup_employee_profile", "check_pto_balance", "lookup_benefits_status", "check_policy_compliance",
                   "create_mock_hr_ticket", "draft_hr_email"}
_STOP = {"the", "a", "an", "is", "are", "i", "my", "me", "do", "can", "what", "how", "of", "to", "for", "in", "on",
         "and", "or", "if", "it", "be", "does", "get", "much", "many", "when", "who", "at", "with", "will"}


def nlu_rewrite(query: str) -> str:
    from ..rag.query import rewrite_query  # light module: no FAISS/sklearn import
    return rewrite_query(query)


def _bracket_citations(text: str) -> str:
    """Turn "(POL-002 §5.1)" or "(POL-004 §5, POL-006 §5)" in tool text into [POL-002 §5.1] markers."""
    def repl(m: re.Match) -> str:
        markers, doc = [], None
        for part in re.finditer(r"(POL-\d{3})?\s*§\s*(\d+(?:\.\d+)*)", m.group(1)):
            doc = part.group(1) or doc  # "§8" after "POL-007 §3," belongs to the same document
            markers.append(f"[{doc} §{part.group(2)}]")
        return " " + " ".join(markers)
    return re.sub(r"\s*\((POL-\d{3}\s*§[^)]*)\)", repl, text)


def _drop_nulls(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _extracted_summary(ex: nlu.Extracted) -> dict:
    return _drop_nulls({
        "days": ex.days, "weeks": ex.weeks, "workdays": ex.workdays,
        "start_date": ex.start_date.isoformat() if ex.start_date else None,
        "end_date": ex.end_date.isoformat() if ex.end_date else None,
        "country": ex.country, "state": ex.state, "amount": ex.amount, "expense_item": ex.expense_item,
    })


def _summarize(name: str, output: dict) -> Any:
    """Short version of a tool output for the trace (the full output can be long)."""
    if "error" in output:
        return output
    if name == "search_policy_documents":
        return {"keyword_coverage": output.get("keyword_coverage"),
                "results": [{"citation": r["citation"], "section": r["section"], "score": r["score"]}
                            for r in output.get("results", [])]}
    if name == "get_policy_section":
        return {"citation": output.get("citation"), "section": output.get("section"), "chars": len(output.get("text", ""))}
    if name == "check_policy_compliance":
        # Keep every rule check: this is the evidence the answer's numbers come from.
        return {"decision": output.get("decision"), "checks": output.get("checks"),
                "required_approvals": output.get("required_approvals"), "next_steps": output.get("next_steps"),
                "citations": output.get("citations")}
    if name == "create_mock_hr_ticket":
        return {"status": output.get("status"), "ticket": output.get("ticket_preview") or output.get("ticket")}
    if name == "draft_hr_email":
        return {"status": output.get("status"), "to": output.get("to"), "subject": output.get("subject"), "sent": False}
    text = json.dumps(output)
    return output if len(text) < 2500 else text[:2500] + " …"


def _compact_for_llm(name: str, output: dict) -> str:
    """Tool results sent back to the LLM, trimmed to keep free-tier token usage low."""
    if name == "search_policy_documents" and "results" in output:
        output = {"keyword_coverage": output.get("keyword_coverage"), "results": [
            {"citation": r["citation"], "title": r["title"], "section": r["section"], "snippet": r["snippet"][:450]}
            for r in output["results"]]}
    text = json.dumps(output, default=str)
    return text if len(text) <= 3500 else text[:3500] + " …(truncated)"

