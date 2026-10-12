"""Safety guardrails applied before and after the agent plans.

These are deliberately simple, deterministic checks (regular expressions plus a
retrieval-coverage signal) so their behaviour is predictable and testable. They run
in both agent modes (LLM and offline rules). Each decision is recorded in the trace.

Before planning:
- scope:      refuse questions that are not about Lumora HR policy or the user's HR data;
- emergency:  if someone may be in immediate danger, point to emergency services first;
- sensitive:  harassment/discrimination/retaliation/safety → escalate to Employee Relations,
              never investigate or judge (POL-012 §4, POL-013 §5-6);
- privacy:    the agent may only use the acting employee's own records (POL-013 §6);
- injection:  instructions that try to override the assistant's rules are ignored.
During/after planning:
- actions:    tickets are created only after explicit confirmation (enforced in the orchestrator);
- citations:  cited sections must exist in the policy library (verified via MCP).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMPLOYEE_ID_RE = re.compile(r"\b[Ee]\d{4}\b")

# Words that make a question HR-related even if the policy wording differs.
HR_TOPIC_RE = re.compile(
    r"\b(pto|paid time off|vacation|time off|day(s)? off|holiday|leave|sick|bereave\w*|parental|maternity|paternity|"
    r"remote|hybrid|work(ing)? from|abroad|relocat\w*|benefit\w*|insurance|medical|dental|vision|401\s?\(?k\)?|hsa|"
    r"pension|rrsp|enrol\w*|stipend|expens\w*|reimburs\w*|per diem|travel|hotel|laptop|equipment|monitor|chair|desk|"
    r"onboard\w*|new hire|payroll|pay ?day|paycheck|salary|overtime|timesheet|bonus|harass\w*|discriminat\w*|bully\w*|"
    r"retaliat\w*|conduct|complain\w*|manager|hr\b|ticket|policy|policies|security|vpn|password|phishing|jury|"
    r"tuition|learning budget|wellness|eap|intern|contractor|part[- ]time|full[- ]time|probation|introductory|"
    r"coworker|co-worker|colleague|boss|supervisor|workplace|inappropriate|unsafe|employee)\b",
    re.IGNORECASE,
)

SENSITIVE_PATTERNS = {
    "harassment": r"\b(harass\w*|unwanted (touch\w*|advances?|comments?)|sexual(ly)? (comment|joke|advance)\w*|inappropriate (touch|comment|message)\w*|groped?)\b",
    "discrimination": r"\b(discriminat\w*|racis[mt]\w*|sexis[mt]\w*|because (of my|i am|i'm) (race|gender|age|religion|disab\w*|pregnan\w*))\b",
    "retaliation": r"\b(retaliat\w*|punished for (reporting|complaining))\b",
    "bullying": r"\b(bull(y|ied|ying)|yell(s|ed|ing) at me|humiliat\w*|hostile (work )?environment)\b",
    "violence_or_threat": r"\b(threat(en|ened|ening)?|violen\w*|weapon|assault\w*|hit me)\b",
}

EMERGENCY_RE = re.compile(
    r"\b(kill (my|him|her|them)self|suicid\w*|hurt (my|him|her)self|end my life|in danger right now|"
    r"going to (hurt|kill)|has a (gun|weapon) (here|now))\b",
    re.IGNORECASE,
)

INJECTION_RE = re.compile(
    r"\b(ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (your|the) (rules|instructions)|"
    r"you are now|act as (an? )?(admin|administrator|hr director)|reveal (your|the) (system )?prompt|developer mode)\b",
    re.IGNORECASE,
)

# Asking the assistant itself to approve or authorize something it has no authority over.
APPROVAL_REQUEST_RE = re.compile(
    r"^\s*(please\s+)?(approve|authori[sz]e|grant)\b|\b(can|could|will|would) you (please )?(approve|authori[sz]e|grant)\b",
    re.IGNORECASE,
)

UNSAFE_REQUEST_MESSAGE = (
    "**Answer:** I can't do that. I can't change my rules, approve requests, or act on your behalf without a "
    "confirmation you give for a specific action.\n\n"
    "**Policy facts:**\n"
    "- PTO must be requested in PeopleHub and approved by your direct manager before the time off begins; chat "
    "approval is not sufficient [POL-002 §4].\n"
    "- Automated assistants must never submit, approve, or change a PeopleHub request, and never create a ticket or "
    "send a message without the employee's explicit confirmation [POL-013 §6].\n\n"
    "**Recommended next step:** tell me the dates you'd like to take off and I'll check your balance and the notice "
    "rules, and draft a message to your manager for you to send."
)

CONFIRM_RE = re.compile(r"^\s*(yes|yep|yeah|confirm(ed)?|go ahead|please (do|create|submit)|do it|create it|submit it|ok(ay)?)\b", re.IGNORECASE)
CANCEL_RE = re.compile(r"^\s*(no|nope|cancel|don'?t|do not|stop)\b", re.IGNORECASE)

ADVICE_DISCLAIMER_RE = re.compile(r"\b(tax(es)?|visa|immigration|lawsuit|sue|legal advice|diagnos\w*)\b", re.IGNORECASE)

OUT_OF_SCOPE_MESSAGE = (
    "I can only help with Lumora HR policies (PTO, holidays, remote work, benefits, leave, expenses, equipment, "
    "security, conduct, payroll) and your own HR records. That question is outside the policy library, so I "
    "won't answer it. Is there an HR question I can help with?"
)

EMERGENCY_MESSAGE = (
    "If you or someone else is in immediate danger, please contact local emergency services now "
    "(911 in the US and Canada, 999 in the UK). The Employee Assistance Program offers free, confidential "
    "24/7 support (POL-008 §7.2), and you can report safety concerns to Employee Relations, which treats them as "
    "P1 urgent (POL-013 §4)."
)


@dataclass
class GuardrailResult:
    employee_id: str | None
    blocked: bool = False
    block_reason: str | None = None
    message: str | None = None
    sensitive_category: str | None = None
    injection_detected: bool = False
    approval_request: bool = False
    needs_advice_disclaimer: bool = False
    hr_topic: bool = False


def resolve_employee_id(message: str, acting_employee_id: str | None) -> tuple[str | None, str | None]:
    """Decide whose records the agent may use.

    Returns (employee_id, privacy_violation_message). The UI sends the "acting as" employee;
    if none is set, an ID typed in the message is treated as the user identifying themself.
    Mentioning someone else's ID while acting as another employee is refused.
    """
    mentioned = {m.upper() for m in EMPLOYEE_ID_RE.findall(message)}
    acting = acting_employee_id.strip().upper() if acting_employee_id else None
    if acting:
        others = mentioned - {acting}
        if others:
            return acting, (
                f"For privacy I can only look up your own records ({acting}), not {', '.join(sorted(others))}. "
                "Managers and HR partners should use PeopleHub for team records (POL-013 §6)."
            )
        return acting, None
    if len(mentioned) == 1:
        return mentioned.pop(), None
    if len(mentioned) > 1:
        return None, "Please tell me which single employee ID is yours; I can only use one person's own records."
    return None, None


def pre_check(message: str, acting_employee_id: str | None) -> GuardrailResult:
    employee_id, privacy_msg = resolve_employee_id(message, acting_employee_id)
    result = GuardrailResult(employee_id=employee_id)
    result.hr_topic = bool(HR_TOPIC_RE.search(message))
    result.injection_detected = bool(INJECTION_RE.search(message))
    result.approval_request = bool(APPROVAL_REQUEST_RE.search(message))
    result.needs_advice_disclaimer = bool(ADVICE_DISCLAIMER_RE.search(message))

    if EMERGENCY_RE.search(message):
        result.blocked, result.block_reason, result.message = True, "emergency", EMERGENCY_MESSAGE
        return result
    if privacy_msg:
        result.blocked, result.block_reason, result.message = True, "privacy", privacy_msg
        return result
    for category, pattern in SENSITIVE_PATTERNS.items():
        if re.search(pattern, message, re.IGNORECASE):
            result.sensitive_category = category
            break
    return result


def is_out_of_scope(hr_topic: bool, keyword_coverage: float, unmatched_terms: list[str] | None = None) -> bool:
    """Out of scope = no HR vocabulary in the question AND the policy library covers its words poorly.

    "Poorly" means under 50% keyword coverage, or under 60% when some of its words never appear in
    the library at all (e.g. "capital" in "What is the capital of France?").
    """
    if hr_topic:
        return False
    return keyword_coverage < 0.5 or (bool(unmatched_terms) and keyword_coverage < 0.6)
