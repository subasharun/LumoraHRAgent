"""Deterministic policy-compliance checks.

Why rules in code (and not only the LLM)? Numeric thresholds such as "20 workdays
abroad" or "5 business days notice" are easy for an LLM to misapply. Encoding them
here gives exact, testable answers, and every check carries the policy citation it
comes from (e.g. "POL-005 §4"), so the final answer stays grounded.

Each check function returns a dict:
    {
      "request_type": ...,
      "decision": "compliant" | "needs_approval" | "not_compliant" | "not_eligible" | "needs_more_info",
      "checks": [{"rule", "passed", "detail", "citation"}],
      "required_approvals": [...],
      "next_steps": [...],
      "citations": [...],
    }
`passed` is True, False, or None (could not be evaluated because input was missing).
"""

from __future__ import annotations

from datetime import date, timedelta

from . import data

RESTRICTED_COUNTRIES = {"cuba", "iran", "north korea", "syria", "russia", "belarus", "crimea", "donetsk", "luhansk"}
HIGH_COST_CITIES = {"new york", "san francisco", "boston", "seattle", "london", "zurich", "tokyo"}

# Lower-case country names → ISO codes for the home-country comparison.
COUNTRY_CODES = {
    "us": "US", "usa": "US", "united states": "US", "america": "US",
    "canada": "CA", "ca": "CA", "uk": "GB", "united kingdom": "GB", "england": "GB", "gb": "GB",
    "portugal": "PT", "spain": "ES", "france": "FR", "germany": "DE", "italy": "IT", "mexico": "MX",
    "india": "IN", "japan": "JP", "brazil": "BR", "ireland": "IE", "netherlands": "NL",
}


def _check(rule: str, passed: bool | None, detail: str, citation: str) -> dict:
    return {"rule": rule, "passed": passed, "detail": detail, "citation": citation}


def _result(request_type: str, checks: list[dict], approvals: list[str], next_steps: list[str], decision: str | None = None) -> dict:
    if decision is None:
        if any(c["passed"] is False for c in checks):
            decision = "not_compliant"
        elif any(c["passed"] is None for c in checks):
            decision = "needs_more_info"
        elif approvals:
            decision = "needs_approval"
        else:
            decision = "compliant"
    citations = sorted({c["citation"] for c in checks})
    return {
        "request_type": request_type,
        "decision": decision,
        "checks": checks,
        "required_approvals": approvals,
        "next_steps": next_steps,
        "citations": citations,
    }


def _eligibility_checks(emp: dict, today: date, program: str, policy: str) -> list[dict]:
    """Shared checks for remote work and work-from-abroad (POL-004 §3.2, POL-005 §3)."""
    checks = []
    etype = emp["employment_type"]
    checks.append(_check(
        "Employment type is eligible",
        etype in ("full_time", "part_time"),
        f"Employment type is '{etype}'. {program} is available to full-time and part-time employees only.",
        f"{policy} §3" if policy == "POL-005" else "POL-004 §3.2",
    ))
    intro = data.in_introductory_period(emp, today)
    checks.append(_check(
        "90-day introductory period completed",
        not intro,
        f"Hire date {emp['hire_date']}; " + ("still in the introductory period." if intro else "introductory period completed."),
        "POL-001 §5",
    ))
    checks.append(_check(
        "Role is remote-eligible",
        bool(emp.get("remote_eligible_role")),
        "Role is designated remote-eligible." if emp.get("remote_eligible_role") else "Role is designated office-required.",
        "POL-004 §3.1",
    ))
    checks.append(_check(
        "Not on a performance improvement plan",
        not emp.get("on_pip"),
        "No active performance improvement plan." if not emp.get("on_pip") else "Employee is on a performance improvement plan.",
        "POL-004 §3.2",
    ))
    return checks


# ---------------------------------------------------------------------------
# PTO (POL-002)
# ---------------------------------------------------------------------------
def pto_balance(emp: dict, today: date) -> dict:
    """Balance summary used by both check_pto_balance and the PTO compliance check."""
    if emp["employment_type"] not in ("full_time", "part_time"):
        return {"eligible": False, "reason": f"{emp['employment_type']} workers do not accrue PTO (POL-001 §3, POL-002 §2)."}
    years = data.tenure_years(emp, today)
    annual_full = 15 if years < 2 else 20 if years < 5 else 25
    annual = round(annual_full * emp["fte"], 2)
    bal = data.pto_data()["balances"].get(emp["employee_id"], {})
    available = round(bal.get("carryover", 0) + bal.get("accrued_ytd", 0) - bal.get("used_ytd", 0) - bal.get("advance_outstanding", 0), 3)
    pending = round(sum(r["days"] for r in bal.get("pending_requests", [])), 3)
    return {
        "eligible": True,
        "as_of": data.pto_data()["as_of"],
        "tenure_years": years,
        "annual_entitlement_days": annual,
        "carryover_days": bal.get("carryover", 0),
        "accrued_ytd_days": bal.get("accrued_ytd", 0),
        "used_ytd_days": bal.get("used_ytd", 0),
        "available_days": available,
        "pending_request_days": pending,
        "available_after_pending_days": round(available - pending, 3),
        "pending_requests": bal.get("pending_requests", []),
        "balance_cap_days": round(30 * emp["fte"], 2),
        "carryover_limit_days": 5,
        "floating_holidays_remaining": bal.get("floating_holidays_remaining", 0),
        "sick_days_remaining": bal.get("sick_days_remaining", 0),
        "in_introductory_period": data.in_introductory_period(emp, today),
        "citations": ["POL-002 §3", "POL-002 §3.1", "POL-002 §3.2"],
    }


def _required_notice(days: float) -> int:
    """Minimum notice in business days for a request of `days` business days (POL-002 §5)."""
    if days <= 1:
        return 2
    if days <= 4:
        return 5
    if days <= 10:
        return 10
    return 20


def check_pto(emp: dict, today: date, days: float | None, start: date | None, end: date | None) -> dict:
    country = emp["work_location"]["country"]
    checks: list[dict] = []
    approvals = ["Direct manager approval in PeopleHub"]
    next_steps: list[str] = []

    balance = pto_balance(emp, today)
    if not balance["eligible"]:
        checks.append(_check("Employee accrues PTO", False, balance["reason"], "POL-002 §2"))
        return _result("pto", checks, [], ["Contact Vendor Management (contractors) or University Programs (interns) about time off."], "not_eligible")
    checks.append(_check("Employee accrues PTO", True, f"{emp['employment_type']} employee, FTE {emp['fte']}.", "POL-002 §2"))

    # Work out the number of PTO days. Holidays inside the range are free (POL-002 §8).
    holidays: list[str] = []
    if start and end:
        days = data.business_days_between(start, end, country)
        holidays = data.holidays_in_range(start, end, country)
    elif start and days and not end:
        # Derive the end date from a start date and a number of business days.
        end, counted = start, 0
        while True:
            if data.is_business_day(end, country):
                counted += 1
                if counted >= days:
                    break
            end += timedelta(days=1)
        holidays = data.holidays_in_range(start, end, country)
    if not days:
        checks.append(_check("Number of PTO days known", None, "Provide the number of days or the start and end dates.", "POL-002 §4"))
        return _result("pto", checks, approvals, ["Tell me the dates or number of days you want to take."])

    remaining = balance["available_after_pending_days"]
    checks.append(_check(
        "Sufficient PTO balance",
        remaining >= days,
        f"Requesting {days:g} business day(s); available after pending requests: {remaining:g} day(s).",
        "POL-002 §6",
    ))
    if balance["in_introductory_period"]:
        checks.append(_check(
            "Introductory period limit (max 3 days before day 90)",
            days <= 3,
            "Employees may use up to 3 days of PTO before day 90 without HR approval.",
            "POL-002 §2",
        ))

    required = _required_notice(days)
    if start:
        notice = data.notice_business_days(today, start, country)
        earliest = data.add_business_days(today, required + 1, country)
        while not data.is_business_day(earliest, country):
            earliest += timedelta(days=1)
        ok = notice >= required
        checks.append(_check(
            f"Minimum notice of {required} business days",
            ok if ok else False,
            f"Request date {today.isoformat()}, first day off {start.isoformat()}: {notice} business day(s) of notice."
            + ("" if ok else f" Earliest start date that meets the notice rule: {earliest.isoformat()}."),
            "POL-002 §5",
        ))
        if not ok:
            next_steps.append(
                "Notice is shorter than the policy minimum. Your manager may still approve a short-notice request at their "
                "discretion (POL-002 §5.1); explain the reason in the request comments, or move the start date to "
                f"{earliest.isoformat()} or later."
            )
    else:
        checks.append(_check(f"Minimum notice of {required} business days", None, "Start date not provided.", "POL-002 §5"))

    if days > 10:
        approvals.append("Department director approval (more than 10 consecutive business days)")
    elif days >= 5:
        next_steps.append("Confirm coverage for any on-call rotation (requests of 5–10 days).")
    if holidays:
        next_steps.append(f"Company holiday(s) in this range are not deducted from PTO: {', '.join(holidays)} (POL-002 §8).")
    next_steps += [
        "Submit the request in PeopleHub before the time off begins; chat approval is not sufficient (POL-002 §4).",
        "Your manager should respond within 3 business days (POL-002 §4).",
    ]

    result = _result("pto", checks, approvals, next_steps)
    # A short-notice request is not forbidden, only discretionary: report it as "needs_approval".
    notice_failed = any(c["rule"].startswith("Minimum notice") and c["passed"] is False for c in checks)
    other_failed = any(c["passed"] is False and not c["rule"].startswith("Minimum notice") for c in checks)
    if notice_failed and not other_failed:
        result["decision"] = "needs_approval"
        result["decision_note"] = "Short notice: approval is at the manager's discretion (POL-002 §5.1)."
    result["requested_days"] = days
    if start:
        result["start_date"] = start.isoformat()
    if end:
        result["end_date"] = end.isoformat()
    return result


# ---------------------------------------------------------------------------
# Temporary remote work inside the home country (POL-004 §4)
# ---------------------------------------------------------------------------
def check_remote_domestic(emp: dict, today: date, destination_state: str | None, calendar_days: int | None, start: date | None) -> dict:
    checks = _eligibility_checks(emp, today, "Temporary remote work", "POL-004")
    approvals: list[str] = []
    next_steps: list[str] = []
    if destination_state and destination_state.strip().upper() == (emp["work_location"]["state_province"] or "").upper():
        next_steps.append("The destination is in your registered state/province, so the cross-state thresholds do not apply.")
    if not calendar_days:
        checks.append(_check("Duration known", None, "Provide the number of calendar days.", "POL-004 §4"))
        return _result("remote_work_domestic", checks, approvals, ["Tell me how many calendar days you plan to work there."])

    if calendar_days <= 14:
        tier = "Manager notification in PeopleHub (no approval needed)"
    elif calendar_days <= 30:
        tier = "Manager approval"
        approvals.append("Manager approval")
    elif calendar_days <= 60:
        tier = "Manager approval and People Operations + Payroll approval"
        approvals += ["Manager approval", "People Operations + Payroll approval"]
    else:
        tier = "Not permitted as temporary work"
    checks.append(_check(
        "Within the 60-calendar-day temporary limit",
        calendar_days <= 60,
        f"{calendar_days} calendar days → tier: {tier}.",
        "POL-004 §4",
    ))
    if calendar_days > 60:
        next_steps.append("Request a permanent Work Location Change at least 45 calendar days before the move (POL-004 §4.1).")
    if 31 <= calendar_days <= 60:
        if start:
            lead = (start - today).days
            checks.append(_check(
                "Submitted at least 30 calendar days in advance",
                lead >= 30, f"{lead} calendar days between today and the start date.", "POL-004 §4",
            ))
        next_steps.append("Submit at least 30 calendar days before the start so Payroll can set up withholding (POL-004 §4).")
    next_steps += [
        "Submit in PeopleHub under Work Arrangements → Temporary Location (POL-004 §10).",
        "Use only your Lumora device and the VPN while away (POL-004 §5, POL-006 §5).",
    ]
    return _result("remote_work_domestic", checks, approvals, next_steps)


# ---------------------------------------------------------------------------
# Work from abroad (POL-005)
# ---------------------------------------------------------------------------
def check_work_from_abroad(
    emp: dict, today: date, destination_country: str | None, workdays: int | None,
    weeks: float | None, start: date | None, end: date | None,
) -> dict:
    checks = _eligibility_checks(emp, today, "Work From Abroad", "POL-005")
    approvals = ["Manager approval", "Global Mobility approval", "Information Security approval"]
    next_steps: list[str] = []
    home = emp["work_location"]["country"]

    if not destination_country:
        checks.append(_check("Destination country known", None, "Provide the destination country.", "POL-005 §5"))
        return _result("work_from_abroad", checks, approvals, ["Tell me which country you want to work from."])

    dest_name = destination_country.strip().lower()
    dest_code = COUNTRY_CODES.get(dest_name, destination_country.strip().upper()[:2])
    if dest_code == home:
        return _result("work_from_abroad", [_check(
            "Destination is outside the home country", False,
            "The destination is in your registered work location country; use the domestic remote-work rules instead.",
            "POL-005 §1")], [], ["Ask about temporary work from another state/province (POL-004 §4)."], "not_compliant")

    restricted = any(r in dest_name for r in RESTRICTED_COUNTRIES)
    checks.append(_check(
        "Destination is not a restricted country", not restricted,
        f"{destination_country} is {'on' if restricted else 'not on'} the restricted list.", "POL-005 §6",
    ))

    # Workdays abroad: explicit number > dates > weeks × 5.
    if not workdays:
        if start and end:
            workdays = data.business_days_between(start, end, home)
        elif weeks:
            workdays = int(round(weeks * 5))
    if not workdays:
        checks.append(_check("Duration known", None, "Provide dates, weeks, or workdays.", "POL-005 §4"))
        return _result("work_from_abroad", checks, approvals, ["Tell me how long you plan to work abroad."])

    used = int(emp.get("wfa_workdays_last_12m", 0))
    total = used + workdays
    within = total <= 20
    checks.append(_check(
        "Within 20 workdays abroad per rolling 12 months",
        within,
        f"Requested ≈{workdays} workdays" + (f" (≈{weeks:g} weeks × 5)" if weeks and not (start and end) else "")
        + f" + {used} already used in the last 12 months = {total}; limit is 20.",
        "POL-005 §4",
    ))

    citizen = dest_code in emp.get("citizenship", [])
    checks.append(_check(
        "Right to work in the destination",
        True if citizen else None,
        "Employee is a citizen of the destination country." if citizen
        else "Not a citizen of the destination: Global Mobility must confirm that short remote work is permitted for visitors.",
        "POL-005 §3",
    ))
    if start:
        lead = (start - today).days
        checks.append(_check("Requested at least 30 calendar days before departure", lead >= 30,
                             f"{lead} calendar days between today and departure.", "POL-005 §5"))

    if not within:
        allowed = max(0, 20 - used)
        next_steps += [
            f"Option A: shorten the work period to {allowed} workdays or fewer (about {allowed / 5:g} weeks) (POL-005 §4).",
            "Option B: combine a shorter work period with PTO; days on approved PTO are not workdays (POL-005 §4).",
            "Option C: request a Global Mobility review via an HR ticket in the 'Global Mobility' category; reviews take 6–8 weeks "
            "and most extended requests without a business need are declined (POL-005 §4).",
        ]
    next_steps += [
        "Submit a Work From Abroad request in PeopleHub at least 30 calendar days before departure (POL-005 §5).",
        "While abroad: Lumora device only, VPN at all times, no Restricted data without written approval (POL-005 §7).",
        "Lumora does not reimburse travel or living costs for personal WFA periods (POL-005 §10).",
        "Tax, payroll and immigration questions go to Global Mobility; Lumora does not give personal tax advice, so "
        "consult a personal tax advisor (POL-005 §8).",
    ]
    result = _result("work_from_abroad", checks, approvals, next_steps)
    if not within and result["decision"] != "not_compliant":
        result["decision"] = "not_compliant"
    result["workdays_requested"] = workdays
    return result


# ---------------------------------------------------------------------------
# Expenses and stipends (POL-007)
# ---------------------------------------------------------------------------
_EXPENSE_ALIASES = {
    "laptop": "computer", "computer": "computer", "tablet": "computer", "phone": "computer", "macbook": "computer",
    "monitor": "monitor",
    "chair": "chair", "office chair": "chair", "ergonomic chair": "chair",
    "desk": "desk", "standing desk": "desk",
    "keyboard": "accessories", "mouse": "accessories", "headset": "accessories", "webcam": "accessories", "lamp": "accessories",
    "monitor arm": "small_ergonomic", "footrest": "small_ergonomic", "laptop stand": "small_ergonomic",
    "meal": "meal", "meals": "meal", "food": "meal",
    "hotel": "hotel", "lodging": "hotel",
    "flight": "flight", "airfare": "flight",
    "course": "learning", "book": "learning", "certification": "learning", "conference": "learning", "learning": "learning",
    "coworking": "coworking", "internet": "internet", "alcohol": "alcohol",
}

_STIPEND_SUBLIMITS = {"chair": 350, "desk": 300, "accessories": 150, "small_ergonomic": 100}


def check_expense(emp: dict, today: date, category: str | None, amount: float | None, travel_type: str | None, city: str | None) -> dict:
    checks: list[dict] = []
    approvals: list[str] = []
    next_steps: list[str] = []
    cat = _EXPENSE_ALIASES.get((category or "").strip().lower(), (category or "").strip().lower())
    etype = emp["employment_type"]

    if not cat:
        checks.append(_check("Expense category known", None, "Tell me what was purchased.", "POL-007 §3"))
        return _result("expense", checks, approvals, ["Describe the item or expense type and amount."])

    if etype == "contractor":
        checks.append(_check("Worker may claim expenses", False,
                             "Contractors may be reimbursed only if their statement of work allows it.", "POL-007 §2"))
        return _result("expense", checks, approvals, ["Check your statement of work or contact Vendor Management."], "not_eligible")

    if cat in ("computer", "monitor"):
        detail = ("Laptops, tablets and phones are never reimbursed; IT issues and replaces them."
                  if cat == "computer" else
                  "Monitors are not covered by the stipend; IT provides one external monitor to hybrid/remote employees on request.")
        checks.append(_check("Item is reimbursable", False, detail, "POL-007 §4" if cat == "computer" else "POL-007 §6.1"))
        next_steps.append("Request the equipment through the IT Service Desk instead (POL-011 §3, §4).")
        return _result("expense", checks, approvals, next_steps, "not_compliant")

    if cat == "alcohol":
        checks.append(_check("Item is reimbursable", False, "Alcohol is reimbursed only as part of approved client entertainment (max 30% of the bill).", "POL-007 §4"))
        return _result("expense", checks, approvals, next_steps, "not_compliant")

    if cat in _STIPEND_SUBLIMITS:
        eligible_type = etype == "full_time" or (etype == "part_time" and emp["fte"] >= 0.75)
        arrangement_ok = emp["work_arrangement"] in ("hybrid", "remote")
        checks.append(_check(
            "Eligible for the one-time home office stipend",
            eligible_type and arrangement_ok,
            f"{etype}, FTE {emp['fte']}, arrangement '{emp['work_arrangement']}'. Requires full-time or ≥0.75 FTE and a hybrid or remote arrangement.",
            "POL-007 §6.1",
        ))
        # Timing: claim within 12 months of the later of hire date / arrangement start, and only once.
        anchor = max(date.fromisoformat(emp["hire_date"]), date.fromisoformat(emp.get("arrangement_start_date", emp["hire_date"])))
        deadline = anchor + timedelta(days=365)
        claimed = bool(emp.get("home_office_stipend_claimed"))
        checks.append(_check(
            "Stipend not already used and claimed within 12 months",
            (not claimed) and today <= deadline,
            ("The one-time stipend has already been claimed." if claimed
             else f"Claim window runs from {anchor.isoformat()} to {deadline.isoformat()}."),
            "POL-007 §6.1",
        ))
        limit = _STIPEND_SUBLIMITS[cat]
        if amount is not None:
            checks.append(_check(
                f"Within the ${limit} sub-limit for this item", amount <= limit,
                f"Amount ${amount:,.2f}; reimbursable up to ${min(amount, limit):,.2f} from the $500 one-time stipend.",
                "POL-007 §6.1",
            ))
        next_steps += [
            "Submit in Lumora Expenses under 'Home Office Stipend' with an itemized receipt within 30 days of purchase (POL-007 §3, §8).",
            "The stipend is $500 once (not per year), claimable within 12 months of your start or of becoming hybrid/remote (POL-007 §6.1).",
        ]
        result = _result("expense", checks, ["Manager approval of the claim"], next_steps)
        # Over the sub-limit is a partial reimbursement, not a rejection of the whole claim.
        if amount is not None and amount > limit and checks[0]["passed"] and checks[1]["passed"]:
            result["decision"] = "partially_compliant"
            result["reimbursable_amount"] = limit
        return result

    if cat == "meal":
        daily = 100 if (travel_type or "").lower().startswith("int") else 75
        if amount is not None:
            checks.append(_check(f"Within the ${daily}/day meal limit", amount <= daily,
                                 f"${amount:,.2f} vs daily limit ${daily}.", "POL-007 §5.4"))
        next_steps.append("Itemized receipts are required for meals of $25 or more (POL-007 §5.4).")
    elif cat == "hotel":
        cap = 350 if (city or "").lower() in HIGH_COST_CITIES else 250
        if amount is not None:
            checks.append(_check(f"Within the ${cap}/night lodging cap", amount <= cap,
                                 f"${amount:,.2f}/night vs cap ${cap} ({city or 'standard city'}).", "POL-007 §5.3"))
        next_steps.append("Rates above the cap need manager approval before booking (POL-007 §5.3).")
    elif cat == "learning":
        eligible = etype == "full_time" or (etype == "part_time" and emp["fte"] >= 0.75)
        checks.append(_check("Eligible for the $1,500 annual learning budget", eligible,
                             f"{etype}, FTE {emp['fte']}.", "POL-007 §6.4"))
        if amount is not None:
            checks.append(_check("Within the $1,500 annual budget", amount <= 1500, f"${amount:,.2f}.", "POL-007 §6.4"))
    elif cat == "coworking":
        ok = emp["work_arrangement"] == "remote"
        checks.append(_check("Remote arrangement (coworking allowance)", ok, f"Arrangement '{emp['work_arrangement']}'.", "POL-007 §6.3"))
        if amount is not None:
            checks.append(_check("Within $150/month", amount <= 150, f"${amount:,.2f}.", "POL-007 §6.3"))
    elif cat == "internet":
        ok = emp["work_arrangement"] == "remote"
        checks.append(_check("Remote arrangement (internet allowance)", ok,
                             "Paid as $50/month through payroll; not claimed as an expense.", "POL-007 §6.2"))
    elif cat == "flight":
        checks.append(_check("Class of travel", None,
                             "Economy ≤6h, premium economy >6h, business >10h with VP approval.", "POL-007 §5.2"))
    else:
        checks.append(_check("Expense is a known category", None,
                             "Not a standard category; it must be necessary, reasonable and business-related.", "POL-007 §3"))

    if amount is not None and amount >= 25:
        next_steps.append("Attach an itemized receipt (required for $25 or more) (POL-007 §3).")
    if amount is not None and amount > 5000:
        approvals.append("Department director approval (claims over $5,000)")
    approvals.insert(0, "Manager approval of the claim")
    next_steps.append("Submit within 30 days of the expense date (POL-007 §3).")
    return _result("expense", checks, approvals, next_steps)
