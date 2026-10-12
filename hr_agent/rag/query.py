"""Query rewriting (kept free of heavy imports so the web app can use it without loading FAISS)."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Query rewriting: expand everyday words into the vocabulary the policies use.
# Each rule is (regex that triggers it, words appended to the query).
# ---------------------------------------------------------------------------
QUERY_EXPANSIONS: list[tuple[str, str]] = [
    (r"\b(vacation|days? off|time off|leave next week)\b", "PTO paid time off request"),
    (r"\bpto\b", "paid time off notice approval balance"),
    (r"\bcarr(y|ies|ied|ying)[ -]?over\b|\brolls? over\b", "carryover unused PTO forfeited"),
    (r"\b(wfh|work(ing)? from home|remote(ly)?)\b", "remote work arrangement"),
    (r"\b(abroad|overseas|another country|internationally)\b", "work from abroad cross-border workdays"),
    # A country name only means "work from abroad" when the question is about working there
    # (otherwise "What is the capital of France?" would look like an HR question).
    (r"\bwork\w*\b.*\b(portugal|spain|france|italy|mexico|india|japan|germany|canada|brazil|uk|england|ireland)\b"
     r"|\b(portugal|spain|france|italy|mexico|india|japan|germany|canada|brazil|ireland)\b.*\bwork\w*\b",
     "work from abroad cross-border workdays"),
    (r"\b(another|different) state\b", "temporary work another state calendar days payroll"),
    (r"\b(laptop|computer|macbook|monitor)\b", "equipment IT laptop not reimbursable"),
    (r"\b(chair|desk|standing desk|home office)\b", "home office stipend"),
    (r"\b(reimburs\w*|expens\w*|claim)\b", "expense reimbursement receipt"),
    (r"\b(401k|401\(k\)|retirement|pension)\b", "401(k) match retirement"),
    (r"\b(insurance|health plan|medical|dental|vision|ppo|hdhp|hsa)\b", "medical benefits plan eligibility"),
    (r"\b(maternity|paternity|baby|adoption|newborn)\b", "parental leave"),
    (r"\b(harass\w*|bully\w*|discriminat\w*|retaliat\w*|hostile|inappropriate)\b",
     "harassment report employee relations investigation"),
    (r"\b(per diem|meals?|hotel|flight|travel)\b", "business travel per diem lodging"),
    (r"\b(sick|ill|doctor)\b", "sick leave"),
    (r"\b(new hire|first day|first week|onboard\w*)\b", "onboarding checklist"),
    (r"\b(paycheck|salary|payday|paid on|pay schedule)\b", "payroll pay schedule"),
    (r"\b(holiday|holidays|thanksgiving|christmas)\b", "company holiday calendar"),
    (r"\b(phish\w*|password|vpn|mfa|security)\b", "information security"),
    (r"\b(funeral|passed away|bereave\w*)\b", "bereavement leave"),
    (r"\b(ticket|escalat\w*)\b", "HR ticket category priority"),
]

# Common English words ignored for keyword matching (a compact list, so the MCP server does not need to
# import scikit-learn just for its stop-word list, which saves memory on the free tier).
STOP_WORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both
but by can could did do does doing down during each either else ever every few for from further get gets got had has
have having he her here hers herself him himself his how however i if in into is it its itself just let like may me
might many more most much must my myself no nor not now of off often on once only or other our ours ourselves out over own
per please same shall she should so some such than that the their theirs them themselves then there these they this
those though through to too under until up upon us very via was we were what when where whether which while who whom
whose why will with within without would yet you your yours yourself yourselves
""".split())

# Everyday words mapped to the word the policies use (applied when checking vocabulary coverage).
SYNONYMS = {
    "grandmother": "grandparent", "grandfather": "grandparent", "grandma": "grandparent", "grandpa": "grandparent",
    "mom": "parent", "mother": "parent", "dad": "parent", "father": "parent", "husband": "spouse", "wife": "spouse",
    "brother": "sibling", "sister": "sibling", "son": "child", "daughter": "child", "kid": "child", "vacation": "pto",
    "wfh": "remote", "dies": "loss", "died": "loss", "death": "loss", "passed": "loss", "reimbursed": "reimbursement", "reimburse": "reimbursement", "paycheck": "pay", "payday": "pay",
}
# Words too generic to signal a gap in the policy library.
_GENERIC = {"lumora", "company", "policy", "policies", "need", "want", "get", "got", "know", "tell", "please", "thanks",
            "question", "help", "like", "would", "could", "should", "allowed", "able", "really", "also", "still", "much",
            "many", "long", "next", "week", "weeks", "day", "days", "month", "months", "year", "years", "today", "tomorrow"}



def rewrite_query(query: str) -> str:
    """Append policy vocabulary for any everyday phrasing found in the query."""
    lowered = query.lower()
    extra = [words for pattern, words in QUERY_EXPANSIONS if re.search(pattern, lowered)]
    extra += sorted({SYNONYMS[t] for t in re.findall(r"[a-z]+", lowered) if t in SYNONYMS})
    return query if not extra else f"{query} ({'; '.join(extra)})"
