"""Access to the synthetic HR data in `mock_data/`.

This module is the only place that reads the JSON files, so the MCP tools stay
small and the data source could later be swapped for SQLite or a real API.

New mock tickets are appended to a separate *runtime* file (data/runtime_tickets.json
by default) so the committed seed data never changes.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from hr_agent.config import get_settings

_ticket_lock = threading.Lock()


def _load(name: str) -> dict:
    path: Path = get_settings().mock_data_dir / name
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def employees() -> dict[str, dict]:
    return {e["employee_id"]: e for e in _load("employees.json")["employees"]}


@lru_cache(maxsize=1)
def pto_data() -> dict:
    return _load("pto_balances.json")


@lru_cache(maxsize=1)
def benefits_data() -> dict:
    return _load("benefits.json")


@lru_cache(maxsize=1)
def holidays() -> dict[str, set[date]]:
    raw = _load("holidays_2026.json")
    return {k: {date.fromisoformat(d) for d in v} for k, v in raw.items() if not k.startswith("_")}


def get_employee(employee_id: str) -> dict | None:
    return employees().get(employee_id.strip().upper())


def tenure_years(employee: dict, today: date) -> float:
    hired = date.fromisoformat(employee["hire_date"])
    return round((today - hired).days / 365.25, 2)


def in_introductory_period(employee: dict, today: date) -> bool:
    """True during the first 90 days after the hire date (POL-001 §5)."""
    return (today - date.fromisoformat(employee["hire_date"])).days < 90


# ---------------------------------------------------------------------------
# Business-day arithmetic (weekends + the employee's country holidays are excluded)
# ---------------------------------------------------------------------------
def is_business_day(d: date, country: str) -> bool:
    return d.weekday() < 5 and d not in holidays().get(country, set())


def business_days_between(start: date, end: date, country: str) -> int:
    """Business days in the inclusive range [start, end]."""
    if end < start:
        return 0
    days, d = 0, start
    while d <= end:
        days += is_business_day(d, country)
        d += timedelta(days=1)
    return days


def notice_business_days(submitted: date, first_day_off: date, country: str) -> int:
    """Business days strictly after the submission date and before the first day off (POL-002 §5)."""
    return business_days_between(submitted + timedelta(days=1), first_day_off - timedelta(days=1), country)


def add_business_days(start: date, n: int, country: str) -> date:
    """The date that is n business days after `start` (used to suggest compliant start dates)."""
    d, count = start, 0
    while count < n:
        d += timedelta(days=1)
        if is_business_day(d, country):
            count += 1
    return d


def holidays_in_range(start: date, end: date, country: str) -> list[str]:
    return sorted(d.isoformat() for d in holidays().get(country, set()) if start <= d <= end)


# ---------------------------------------------------------------------------
# Tickets (mock write operation)
# ---------------------------------------------------------------------------
def _runtime_ticket_path() -> Path:
    return get_settings().tickets_runtime_path


def list_tickets(employee_id: str) -> list[dict]:
    seed = [t for t in _load("tickets.json")["tickets"] if t["employee_id"] == employee_id]
    path = _runtime_ticket_path()
    runtime = json.loads(path.read_text()) if path.exists() else []
    return seed + [t for t in runtime if t["employee_id"] == employee_id]


def save_ticket(ticket: dict) -> dict:
    """Append a ticket to the runtime file and assign an ID. Nothing real is created."""
    path = _runtime_ticket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ticket_lock:
        existing = json.loads(path.read_text()) if path.exists() else []
        ticket = {
            **ticket,
            "ticket_id": f"MOCK-HR-{len(existing) + 1:04d}",
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mock": True,
        }
        existing.append(ticket)
        path.write_text(json.dumps(existing, indent=2))
    return ticket
