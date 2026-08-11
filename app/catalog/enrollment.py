"""app/catalog/enrollment.py — buy-flow helpers shared by the enroll landing
page (app/catalog/routes.py) and the subscribed-courses page
(app/profiles/routes.py).

Product deliberately does not carry `format` (mode/cohort_start/access) — it
stays in the curated JSON document (app/catalog/loader.py::as_product_row).
So the buy page reads that document by slug at request time, and freezes what
it finds onto the Enrollment row: what a buyer purchased must not shift
underneath them if the course file is edited later.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.catalog.loader import load_one

_ACCESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(year|month|week|day)s?", re.IGNORECASE)
_DAYS_PER_UNIT = {"day": 1, "week": 7, "month": 30, "year": 365}


def parse_access_window(text: str) -> timedelta | None:
    """"1.5 years dashboard access" → timedelta. None means lifetime/unknown —
    an unparseable string must default to "never expires", not to a guessed
    date, the same stale-data rule freshness.py applies to cohort dates."""
    if not text or re.search(r"lifetime|forever|unlimited", text, re.IGNORECASE):
        return None
    m = _ACCESS_RE.search(text)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    return timedelta(days=n * _DAYS_PER_UNIT[unit])


def _future_cohort_start(fmt: dict) -> date | None:
    """format.cohort_start, but only if it is genuinely ahead of today —
    schema §3's rule against stale dates reaching persuasion copy, applied
    here the same way freshness._future_cohort applies it to ranking."""
    raw = fmt.get("cohort_start")
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    return parsed if parsed >= date.today() else None


def course_purchase_snapshot(slug: str) -> dict:
    """What the enroll page shows and what gets frozen onto the Enrollment
    row. Falls back to quiet defaults when the slug has no curated JSON file
    (e.g. a course added only through /admin) — a course is still sellable
    without cohort/perks detail."""
    course = load_one(slug) or {}
    fmt = course.get("format") or {}
    declared_mode = fmt.get("mode") or "self-paced"
    cohort = _future_cohort_start(fmt) if declared_mode in ("live", "hybrid") else None
    return {
        "mode": declared_mode,
        "is_live": declared_mode in ("live", "hybrid"),
        "cohort_start": datetime.combine(cohort, datetime.min.time()) if cohort else None,
        "access_text": fmt.get("access") or "",
        "duration": fmt.get("duration") or "",
        "schedule": fmt.get("schedule") or "",
        "perks": course.get("perks") or [],
        "mentors": course.get("mentors") or [],
    }


def enrollment_status(enrollment) -> str:
    """"active" or "expired", derived from access_expires_at at read time —
    see Enrollment's docstring for why nothing stores this."""
    if enrollment.access_expires_at and enrollment.access_expires_at < datetime.utcnow():
        return "expired"
    return "active"
