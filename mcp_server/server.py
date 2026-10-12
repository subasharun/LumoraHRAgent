"""Lumora HR MCP server.

Exposes 9 tools over the Model Context Protocol (MCP). The agent never imports
these functions directly: it discovers them with `tools/list` and calls them with
`tools/call` through an MCP client session.

Run it standalone:
    python -m mcp_server.server                      # stdio transport (default; used by the web app)
    python -m mcp_server.server --transport http     # Streamable HTTP on :8765/mcp (separate service)

Tools
  Policy evidence (RAG):  search_policy_documents, get_policy_section, list_policy_documents
  Structured mock data:   lookup_employee_profile, check_pto_balance, lookup_benefits_status
  Rules engine:           check_policy_compliance
  Mock actions:           create_mock_hr_ticket (confirmation-gated), draft_hr_email (draft only)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from hr_agent.config import get_settings
from hr_agent.rag.index import PolicyIndex, load_or_build_index

from . import compliance, data

# Logs go to stderr: on the stdio transport, stdout is reserved for MCP protocol messages.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[mcp-server] %(levelname)s %(message)s")
log = logging.getLogger("mcp_server")

mcp = MCPServer(
    name="lumora-hr",
    instructions=(
        "HR tools for the fictional company Lumora Systems. Use search_policy_documents for policy evidence "
        "and cite doc_id + section. Employee tools return synthetic data. create_mock_hr_ticket only creates a "
        "ticket when confirm=true, which requires explicit user confirmation."
    ),
)

_index: PolicyIndex | None = None

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def get_index() -> PolicyIndex:
    """Load the vector index once per process (first call builds it if missing)."""
    global _index
    if _index is None:
        _index = load_or_build_index(get_settings())
        log.info("Policy index loaded: %s (%d chunks)", _index.meta.get("backend"), len(_index.chunks))
    return _index


def _today() -> date:
    return get_settings().today()


def _employee_or_error(employee_id: str) -> tuple[dict | None, dict | None]:
    """Return (employee, None) or (None, error-dict). Errors are returned, not raised, so the
    agent can explain them (e.g. ask the user for a valid ID)."""
    if not employee_id or not employee_id.strip():
        return None, {"error": "missing_employee_id", "message": "An employee ID (e.g. E1003) is required."}
    emp = data.get_employee(employee_id)
    if emp is None:
        return None, {"error": "employee_not_found", "message": f"No employee with ID '{employee_id}' in the mock directory."}
    return emp, None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


# ===========================================================================
# Policy evidence (RAG)
# ===========================================================================
@mcp.tool(annotations=READ_ONLY)
def search_policy_documents(
    query: Annotated[str, Field(description="Natural-language question or keywords, e.g. 'notice required for 3 days PTO'.")],
    k: Annotated[int, Field(ge=1, le=10, description="Number of chunks to return.")] = 5,
    doc_ids: Annotated[list[str] | None, Field(description="Optional filter, e.g. ['POL-002'] to search only the PTO policy.")] = None,
) -> dict:
    """Semantic search over the Lumora HR policy library. Returns the top-k policy chunks with
    doc_id, title, section, a citation string such as 'POL-002 §5', the snippet text, and a score.
    Always use this before stating any policy fact."""
    index = get_index()
    settings = get_settings()
    rerank = settings.rerank == "on" or (settings.rerank == "auto" and index.meta.get("backend") != "tfidf")
    hits = index.search(query, k=k, doc_ids=doc_ids, rerank=rerank, alpha=settings.rerank_alpha)
    return {
        "query": query,
        "results": [h.to_dict() for h in hits],
        "keyword_coverage": round(index.keyword_coverage(query), 3),
        # Words from the question that appear nowhere in the library: a hint the policy may not cover it.
        "unmatched_terms": index.unmatched_terms(query),
        "embedding_backend": index.meta.get("backend"),
    }


@mcp.tool(annotations=READ_ONLY)
def get_policy_section(
    doc_id: Annotated[str, Field(description="Policy document ID, e.g. 'POL-005'.")],
    section: Annotated[str, Field(description="Section number such as '4' or '5.1', or words from the heading.")],
) -> dict:
    """Return the full text of one section of a policy document (use after search to read a rule in full)."""
    chunks = get_index().get_section(doc_id.strip().upper(), section)
    if not chunks:
        return {"error": "section_not_found", "message": f"No section '{section}' in {doc_id}."}
    first = chunks[0]
    return {
        "doc_id": first.doc_id,
        "title": first.title,
        "section": first.section,
        "citation": f"{first.doc_id} §{first.section_number}" if first.section_number else first.doc_id,
        # Overlapping windows of one long section are joined back together.
        "text": "\n".join(c.text for c in chunks),
    }


@mcp.tool(annotations=READ_ONLY)
def list_policy_documents() -> dict:
    """List all policy documents (ID, title, source file, section headings) in the library."""
    return {"documents": get_index().list_documents()}


# ===========================================================================
# Structured mock data
# ===========================================================================
@mcp.tool(annotations=READ_ONLY)
def lookup_employee_profile(
    employee_id: Annotated[str, Field(description="Employee ID, e.g. 'E1003'.")],
) -> dict:
    """Look up an employee's (synthetic) profile: title, department, employment type, FTE, hire date,
    tenure, work arrangement, work location, office, manager, and introductory-period status."""
    emp, err = _employee_or_error(employee_id)
    if err:
        return err
    today = _today()
    manager = data.get_employee(emp["manager_id"]) if emp.get("manager_id") else None
    return {
        "employee_id": emp["employee_id"],
        "name": emp["name"],
        "title": emp["title"],
        "department": emp["department"],
        "employment_type": emp["employment_type"],
        "fte": emp["fte"],
        "flsa_status": emp["flsa_status"],
        "hire_date": emp["hire_date"],
        "tenure_years": data.tenure_years(emp, today),
        "in_introductory_period": data.in_introductory_period(emp, today),
        "work_arrangement": emp["work_arrangement"],
        "arrangement_start_date": emp.get("arrangement_start_date"),
        "home_office_stipend_claimed": emp.get("home_office_stipend_claimed", False),
        "remote_eligible_role": emp["remote_eligible_role"],
        "office": emp["office"],
        "work_location": emp["work_location"],
        "citizenship": emp["citizenship"],
        "wfa_workdays_last_12m": emp.get("wfa_workdays_last_12m", 0),
        "manager": {"employee_id": manager["employee_id"], "name": manager["name"], "email": manager["email"]} if manager else None,
        "email": emp["email"],
        "data_notice": "Synthetic record for a course project.",
    }


@mcp.tool(annotations=READ_ONLY)
def check_pto_balance(
    employee_id: Annotated[str, Field(description="Employee ID, e.g. 'E1002'.")],
) -> dict:
    """Return the employee's PTO balance (available days, pending requests, accrual tier, cap,
    carryover limit, floating holidays and sick days remaining). Contractors and interns do not accrue PTO."""
    emp, err = _employee_or_error(employee_id)
    if err:
        return err
    return {"employee_id": emp["employee_id"], **compliance.pto_balance(emp, _today())}


@mcp.tool(annotations=READ_ONLY)
def lookup_benefits_status(
    employee_id: Annotated[str, Field(description="Employee ID, e.g. 'E1015'.")],
) -> dict:
    """Return the employee's current benefit elections and which benefit programs they are eligible
    for based on employment type and FTE (POL-001 §4, POL-008 §2)."""
    emp, err = _employee_or_error(employee_id)
    if err:
        return err
    etype, fte = emp["employment_type"], emp["fte"]
    benefits_eligible = etype == "full_time" or (etype == "part_time" and fte >= 0.75)
    today = _today()
    eligibility = {
        "medical_dental_vision": benefits_eligible,
        "retirement_plan": etype in ("full_time", "part_time"),
        "life_insurance": benefits_eligible,
        "wellness_stipend": etype == "full_time",
        "tuition_reimbursement": etype == "full_time" and data.tenure_years(emp, today) >= 0.5,
        "learning_budget": benefits_eligible,
        "home_office_stipend": benefits_eligible and emp["work_arrangement"] in ("hybrid", "remote"),
        "parental_leave": etype in ("full_time", "part_time") and data.tenure_years(emp, today) >= 0.5,
        "employee_assistance_program": True,
    }
    return {
        "employee_id": emp["employee_id"],
        "employment_type": etype,
        "fte": fte,
        "country": emp["work_location"]["country"],
        "eligibility": eligibility,
        "elections": data.benefits_data()["elections"].get(emp["employee_id"]),
        "citations": ["POL-001 §4", "POL-008 §2", "POL-008 §3"],
    }


# ===========================================================================
# Rules engine
# ===========================================================================
@mcp.tool(annotations=READ_ONLY)
def check_policy_compliance(
    employee_id: Annotated[str, Field(description="Employee ID the request is for.")],
    request_type: Annotated[
        Literal["pto", "remote_work_domestic", "work_from_abroad", "expense"],
        Field(description="pto = time off; remote_work_domestic = temporary work from another state/province; "
                          "work_from_abroad = working from another country; expense = reimbursement or stipend."),
    ],
    start_date: Annotated[str | None, Field(description="ISO date YYYY-MM-DD of the first day (PTO / travel).")] = None,
    end_date: Annotated[str | None, Field(description="ISO date YYYY-MM-DD of the last day.")] = None,
    days: Annotated[float | None, Field(description="PTO: number of business days requested.")] = None,
    weeks: Annotated[float | None, Field(description="Work from abroad / remote: duration in weeks.")] = None,
    calendar_days: Annotated[int | None, Field(description="Remote domestic: total calendar days away.")] = None,
    workdays: Annotated[int | None, Field(description="Work from abroad: number of workdays abroad.")] = None,
    destination_country: Annotated[str | None, Field(description="Work from abroad: destination country, e.g. 'Portugal'.")] = None,
    destination_state: Annotated[str | None, Field(description="Remote domestic: destination state/province code, e.g. 'CO'.")] = None,
    expense_category: Annotated[str | None, Field(description="Expense: e.g. 'laptop', 'chair', 'desk', 'meal', 'hotel', 'learning', 'coworking'.")] = None,
    amount: Annotated[float | None, Field(description="Expense: amount in USD.")] = None,
    travel_type: Annotated[Literal["domestic", "international"] | None, Field(description="Expense: travel type for meals.")] = None,
    city: Annotated[str | None, Field(description="Expense: city for hotel caps.")] = None,
) -> dict:
    """Check a concrete request against Lumora policy rules using the employee's mock data.
    Returns a decision (compliant / needs_approval / partially_compliant / not_compliant / not_eligible /
    needs_more_info), each rule checked with its policy citation, required approvals, and next steps."""
    emp, err = _employee_or_error(employee_id)
    if err:
        return err
    today = _today()
    start, end = _parse_date(start_date), _parse_date(end_date)
    if request_type == "pto":
        result = compliance.check_pto(emp, today, days, start, end)
    elif request_type == "remote_work_domestic":
        cal = calendar_days or (int(round(weeks * 7)) if weeks else None) or ((end - start).days + 1 if start and end else None)
        result = compliance.check_remote_domestic(emp, today, destination_state, cal, start)
    elif request_type == "work_from_abroad":
        result = compliance.check_work_from_abroad(emp, today, destination_country, workdays, weeks, start, end)
    else:
        result = compliance.check_expense(emp, today, expense_category, amount, travel_type, city)
    return {"employee_id": emp["employee_id"], "evaluated_on": today.isoformat(), **result}


# ===========================================================================
# Mock actions
# ===========================================================================
TicketCategory = Literal[
    "Time Off", "Benefits", "Leave", "Work Location", "Global Mobility", "Payroll",
    "Expenses", "Employment Records", "Accommodations", "Employee Relations",
]
_DEFAULT_PRIORITY = {"Employee Relations": "P1", "Global Mobility": "P2", "Payroll": "P2", "Leave": "P2"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def create_mock_hr_ticket(
    employee_id: Annotated[str, Field(description="Employee ID the ticket is for.")],
    category: Annotated[TicketCategory, Field(description="Ticket category from POL-013 §3.")],
    summary: Annotated[str, Field(description="One-sentence summary of the request.")],
    details: Annotated[str | None, Field(description="Relevant dates, policy sections reviewed, etc. No medical details.")] = None,
    confirm: Annotated[bool, Field(description="false = return a preview only (default). true = create the mock ticket; "
                                                 "only allowed after the user explicitly confirms.")] = False,
) -> dict:
    """Create a MOCK HR ticket (no real system is touched). With confirm=false it only returns a preview
    and asks for confirmation. Employee Relations tickets are automatically priority P1 (POL-013 §4)."""
    emp, err = _employee_or_error(employee_id)
    if err:
        return err
    ticket = {
        "employee_id": emp["employee_id"],
        "category": category,
        "priority": _DEFAULT_PRIORITY.get(category, "P3"),
        "summary": summary.strip(),
        "details": (details or "").strip(),
        "restricted": category in ("Employee Relations", "Leave", "Accommodations"),
    }
    if not confirm:
        return {
            "status": "preview",
            "confirmation_required": True,
            "ticket_preview": ticket,
            "message": "No ticket has been created. Ask the user to confirm before creating it.",
            "citations": ["POL-013 §3", "POL-013 §4", "POL-013 §6"],
        }
    saved = data.save_ticket(ticket)
    return {
        "status": "created",
        "ticket": saved,
        "message": f"Mock ticket {saved['ticket_id']} created (synthetic; no real HR system was contacted).",
        "expected_first_response": {"P1": "1 business day", "P2": "2 business days", "P3": "3 business days"}.get(saved["priority"]),
        "citations": ["POL-013 §4"],
    }


@mcp.tool(annotations=READ_ONLY)
def draft_hr_email(
    employee_id: Annotated[str, Field(description="Employee ID of the sender.")],
    recipient: Annotated[Literal["manager", "hr", "global_mobility"], Field(description="Who the draft is addressed to.")],
    purpose: Annotated[
        Literal["pto_request", "remote_work_request", "work_from_abroad_request", "expense_question", "general"],
        Field(description="What the message is about."),
    ],
    key_points: Annotated[list[str], Field(description="Short facts to include, e.g. dates, days, policy citations.")],
) -> dict:
    """Draft (never send) an email for the employee to review and send themselves."""
    emp, err = _employee_or_error(employee_id)
    if err:
        return err
    manager = data.get_employee(emp["manager_id"]) if emp.get("manager_id") else None
    to = {
        "manager": manager["email"] if manager else "your-manager@lumora.example",
        "hr": "people-ops@lumora.example",
        "global_mobility": "global-mobility@lumora.example",
    }[recipient]
    greeting = (manager["name"].split(" (")[0].split()[0] if (recipient == "manager" and manager) else "team")
    subjects = {
        "pto_request": "PTO request",
        "remote_work_request": "Temporary remote work request",
        "work_from_abroad_request": "Work From Abroad request",
        "expense_question": "Question about an expense",
        "general": "HR question",
    }
    openers = {
        "pto_request": "I'd like to request some paid time off.",
        "remote_work_request": "I'd like to request approval to work temporarily from another location.",
        "work_from_abroad_request": "I'd like to request approval to work from abroad for a short period.",
        "expense_question": "I have a question about an expense claim.",
        "general": "I have an HR question.",
    }
    first_name = emp["name"].split(" (")[0].split()[0]
    bullets = "\n".join(f"- {p}" for p in key_points)
    body = (
        f"Hi {greeting},\n\n"
        f"{openers[purpose]} Details:\n{bullets}\n\n"
        "I'll submit the formal request in PeopleHub as the policy requires. "
        "Please let me know if you need anything else from me.\n\n"
        f"Thanks,\n{first_name}"
    )
    return {
        "status": "draft",
        "sent": False,
        "to": to,
        "subject": f"{subjects[purpose]} – {emp['employee_id']}",
        "body": body,
        "note": "DRAFT ONLY – nothing was sent. Review and send it yourself.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Lumora HR MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()
    get_index()  # warm up before accepting requests so the first tool call is fast
    if args.transport == "http":
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
