"""Lightweight intent detection and argument extraction (no LLM).

Used by the offline rule planner, and by both modes to turn relative dates such
as "next week" into concrete ISO dates (so the LLM is not left to guess calendars).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "a couple of": 2, "a couple": 2, "a few": 3, "couple of": 2,
}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

COUNTRIES = ["portugal", "spain", "france", "germany", "italy", "mexico", "india", "japan", "brazil", "ireland",
             "netherlands", "canada", "united kingdom", "uk", "england", "united states", "usa",
             "cuba", "iran", "north korea", "syria", "russia", "belarus"]
US_STATES = {
    "alabama": "AL", "arizona": "AZ", "california": "CA", "colorado": "CO", "florida": "FL", "georgia": "GA",
    "illinois": "IL", "maine": "ME", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "new york": "NY",
    "north carolina": "NC", "ohio": "OH", "oregon": "OR", "pennsylvania": "PA", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "virginia": "VA", "washington": "WA", "wisconsin": "WI", "ontario": "ON", "british columbia": "BC",
    "quebec": "QC", "alberta": "AB",
}
EXPENSE_ITEMS = ["standing desk", "office chair", "ergonomic chair", "monitor arm", "laptop stand", "laptop", "computer",
                 "macbook", "tablet", "phone", "monitor", "chair", "desk", "keyboard", "mouse", "headset", "webcam", "lamp",
                 "hotel", "flight", "meal", "dinner", "lunch", "course", "certification", "conference", "book",
                 "coworking", "internet", "alcohol"]

_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted((re.escape(w) for w in NUMBER_WORDS), key=len, reverse=True)) + r")"


def _to_number(token: str) -> float:
    token = token.lower().strip()
    return float(token) if re.match(r"^\d", token) else float(NUMBER_WORDS.get(token, 0))


@dataclass
class Extracted:
    intent: str = "policy_question"
    days: float | None = None
    weeks: float | None = None
    calendar_days: int | None = None
    workdays: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    country: str | None = None
    state: str | None = None
    amount: float | None = None
    expense_item: str | None = None
    wants_ticket: bool = False
    wants_email: bool = False
    notes: list[str] = field(default_factory=list)


def next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) or 7)


def extract_dates(text: str, today: date) -> tuple[date | None, date | None, list[str]]:
    """Find a start (and maybe end) date. Returns (start, end, notes about assumptions)."""
    lowered = text.lower()
    notes: list[str] = []
    found: list[date] = []

    for m in re.finditer(r"\b(20\d\d)-(\d\d)-(\d\d)\b", lowered):
        found.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    month_re = r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d\d))?\b"
    for m in re.finditer(month_re, lowered):
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(year, MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            continue
        if not m.group(3) and d < today:
            d = date(year + 1, d.month, d.day)  # "March 3" asked in October means next March
        found.append(d)

    if not found:
        if "next week" in lowered:
            found.append(next_monday(today))
            notes.append(f"Interpreted 'next week' as starting Monday {found[0].isoformat()}.")
        elif "tomorrow" in lowered:
            found.append(today + timedelta(days=1))
        else:
            m = re.search(r"\b(next|this|on) (" + "|".join(WEEKDAYS) + r")\b", lowered)
            if m:
                target = WEEKDAYS.index(m.group(2))
                if m.group(1) == "next":
                    # "next Friday" = the Friday of next calendar week.
                    d = next_monday(today) + timedelta(days=target)
                else:
                    # "on/this Friday" = the next Friday after today.
                    d = today + timedelta(days=((target - today.weekday()) % 7) or 7)
                found.append(d)
                notes.append(f"Interpreted '{m.group(0)}' as {d.isoformat()}.")
    found = sorted(set(found))
    start = found[0] if found else None
    end = found[1] if len(found) > 1 else None
    return start, end, notes


def extract(text: str, today: date) -> Extracted:
    lowered = text.lower()
    ex = Extracted()

    # --- durations -------------------------------------------------------
    m = re.search(_NUM + r"\s*(?:business |work(?:ing)? )?(workdays?|days?)\b", lowered)
    if m:
        value = _to_number(m.group(1))
        if "workday" in m.group(2) or "business" in m.group(0) or "work" in m.group(0):
            ex.workdays = int(value)
        ex.days = value
    m = re.search(_NUM + r"\s*weeks?\b", lowered)
    if m:
        ex.weeks = _to_number(m.group(1))
    m = re.search(_NUM + r"\s*months?\b", lowered)
    if m and not ex.weeks:
        ex.weeks = _to_number(m.group(1)) * 4.33
        ex.notes.append(f"Converted {m.group(0)} to about {ex.weeks:.0f} weeks.")

    ex.start_date, ex.end_date, notes = extract_dates(text, today)
    ex.notes += notes

    # --- places, money, items -------------------------------------------
    for country in COUNTRIES:
        if re.search(rf"\b{re.escape(country)}\b", lowered):
            ex.country = country.title() if len(country) > 3 else country.upper()
            break
    for name, code in US_STATES.items():
        if re.search(rf"\b{name}\b", lowered):
            ex.state = code
            break
    m = re.search(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)|(\d[\d,]*(?:\.\d{1,2})?)\s*(?:dollars|usd)\b", lowered)
    if m:
        ex.amount = float((m.group(1) or m.group(2)).replace(",", ""))
    for item in EXPENSE_ITEMS:
        if re.search(rf"\b{item}s?\b", lowered):
            ex.expense_item = {"dinner": "meal", "lunch": "meal", "office chair": "chair", "ergonomic chair": "chair",
                               "macbook": "laptop", "computer": "laptop"}.get(item, item)
            break

    ex.wants_ticket = bool(re.search(r"\b(open|create|file|submit|raise|log)\b.{0,20}\b(ticket|case)\b", lowered))
    ex.wants_email = bool(re.search(r"\b(draft|write|compose|prepare)\b.{0,30}\b(email|message|note)\b", lowered))
    ex.intent = detect_intent(lowered, ex)
    return ex


def detect_intent(lowered: str, ex: Extracted) -> str:
    """Pick the workflow. Order matters: the most specific signals are checked first."""
    from .guardrails import SENSITIVE_PATTERNS  # local import avoids a cycle

    if any(re.search(p, lowered) for p in SENSITIVE_PATTERNS.values()):
        return "hr_case_triage"
    # A *request* ("can I ...", "I'd like to ...") or concrete details (country, duration, dates) separates a
    # personal workflow from a general policy question such as "What rules apply when I work abroad?".
    request = re.search(r"\b(can|could|may) i\b|\bi('d| would)? (like|want|need|plan|hope) to\b|\bam i allowed\b|"
                        r"\b(i'm|i am) (planning|thinking about|hoping)\b", lowered)
    concrete = ex.weeks or ex.days or ex.workdays or ex.start_date
    work_words = re.search(r"\b(work(ing)?|remote(ly)?|wfh|based)\b", lowered)
    abroad = ex.country or re.search(r"\b(abroad|overseas|another country|internationally)\b", lowered)
    if work_words and abroad and (request or ex.country or concrete):
        if ex.country and ex.country.lower() in ("united states", "usa"):
            return "remote_work_domestic"
        return "work_from_abroad"
    if work_words and (ex.state or re.search(r"\b(another|different|other) (state|province)\b", lowered)) and (request or concrete):
        return "remote_work_domestic"
    if re.search(r"\b(reimburs\w*|expens\w*|stipend|claim|pay (me )?back|buy|bought|purchase\w*)\b", lowered) and (ex.expense_item or ex.amount):
        return "expense_check"
    pto_words = re.search(r"\b(pto|vacation|time off|days? off|holiday|leave)\b", lowered)
    take_request = re.search(r"\b(can|could|may) i (take|have|use|book|get)\b|\bi('d| would)? (like|want|plan|hope) to "
                             r"(take|use|book|request|have)\b", lowered)
    if take_request and (pto_words or concrete):
        return "pto_request"
    if re.search(r"\b(pto|vacation|time off)\b", lowered) and re.search(
            r"\b(how many|how much)\b.*\b(do i have|have i got|i have|left|remaining)\b|\b(what('s| is)|check|show) my (pto )?balance\b", lowered):
        return "pto_balance"
    if re.search(r"\b(benefit\w*|enrol\w*|medical|dental|vision|insurance|401\s?\(?k\)?|hsa|eligib\w*)\b", lowered) and re.search(
        r"\b(i|my|me|am i)\b", lowered):
        return "benefits_triage"
    return "policy_question"
