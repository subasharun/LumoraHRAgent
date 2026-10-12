"""Unit tests for the deterministic policy rules behind check_policy_compliance."""

from datetime import date

from hr_agent.agent import nlu
from mcp_server import compliance, data

TODAY = date(2026, 10, 12)  # a Monday


def emp(employee_id):
    return data.get_employee(employee_id)


def test_work_from_abroad_six_weeks_exceeds_limit():
    r = compliance.check_work_from_abroad(emp("E1003"), TODAY, "Portugal", None, 6, None, None)
    assert r["decision"] == "not_compliant"
    assert r["workdays_requested"] == 30
    assert "POL-005 §4" in r["citations"]


def test_work_from_abroad_four_weeks_needs_approval():
    r = compliance.check_work_from_abroad(emp("E1005"), TODAY, "Portugal", 10, None, None, None)
    # E1005 already used 10 workdays; 10 more = 20, still within the limit. Citizen of PT.
    assert r["decision"] == "needs_approval"


def test_restricted_country_and_intern():
    assert compliance.check_work_from_abroad(emp("E1003"), TODAY, "Russia", 5, None, None, None)["decision"] == "not_compliant"
    r = compliance.check_work_from_abroad(emp("E1006"), TODAY, "Spain", 5, None, None, None)
    assert any(c["rule"] == "Employment type is eligible" and c["passed"] is False for c in r["checks"])


def test_pto_short_notice_is_manager_discretion():
    r = compliance.check_pto(emp("E1002"), TODAY, 3, date(2026, 10, 19), None)
    assert r["decision"] == "needs_approval"
    assert r["end_date"] == "2026-10-21"
    notice = next(c for c in r["checks"] if c["rule"].startswith("Minimum notice"))
    assert notice["passed"] is False and "2026-10-20" in notice["detail"]


def test_pto_with_enough_notice_and_balance():
    r = compliance.check_pto(emp("E1001"), TODAY, 2, date(2026, 11, 2), None)
    assert r["decision"] == "needs_approval"  # manager approval is always required
    assert all(c["passed"] for c in r["checks"])


def test_pto_insufficient_balance_and_ineligible():
    r = compliance.check_pto(emp("E1005"), TODAY, 5, date(2026, 12, 7), None)
    assert r["decision"] == "not_compliant"  # only 3.75 days available
    assert compliance.check_pto(emp("E1008"), TODAY, 1, None, None)["decision"] == "not_eligible"


def test_holidays_are_not_counted():
    # Nov 23-27 2026 contains US Thanksgiving (26th) and the day after (27th): 3 PTO days.
    r = compliance.check_pto(emp("E1001"), TODAY, None, date(2026, 11, 23), date(2026, 11, 27))
    assert r["requested_days"] == 3


def test_expense_rules():
    assert compliance.check_expense(emp("E1001"), TODAY, "laptop", 1500, None, None)["decision"] == "not_compliant"
    chair = compliance.check_expense(emp("E1001"), TODAY, "chair", 400, None, None)
    assert chair["decision"] == "partially_compliant" and chair["reimbursable_amount"] == 350
    # E1004 is part-time 0.6 FTE and office-based: not eligible for the stipend.
    assert compliance.check_expense(emp("E1004"), TODAY, "chair", 200, None, None)["decision"] == "not_compliant"
    assert compliance.check_expense(emp("E1008"), TODAY, "meal", 40, None, None)["decision"] == "not_eligible"


def test_remote_domestic_tiers():
    r = compliance.check_remote_domestic(emp("E1001"), TODAY, "CO", 42, None)
    assert r["decision"] == "needs_approval"
    assert "People Operations + Payroll approval" in r["required_approvals"]
    assert compliance.check_remote_domestic(emp("E1001"), TODAY, "CO", 90, None)["decision"] == "not_compliant"


def test_nlu_dates_and_durations():
    ex = nlu.extract("Can I take 3 days of PTO next week?", TODAY)
    assert ex.intent == "pto_request" and ex.days == 3 and ex.start_date == date(2026, 10, 19)
    ex = nlu.extract("Can I work from Portugal for six weeks?", TODAY)
    assert ex.intent == "work_from_abroad" and ex.weeks == 6 and ex.country == "Portugal"
    ex = nlu.extract("Can I expense a $400 office chair?", TODAY)
    assert ex.intent == "expense_check" and ex.amount == 400 and ex.expense_item == "chair"
    ex = nlu.extract("PTO from November 23 to November 27", TODAY)
    assert ex.start_date == date(2026, 11, 23) and ex.end_date == date(2026, 11, 27)
