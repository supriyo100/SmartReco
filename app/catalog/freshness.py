"""app/catalog/freshness.py — deterministic live-vs-recorded and recency scoring
(data/COURSE_SCHEMA.md §6). Consumed by fusion_rank as the 0.10 `freshness` term.

Why this exists: people want *live* and they want *recent*. A cohort starting in
four weeks is a materially better recommendation than the same syllabus recorded
eighteen months ago, and embedding similarity cannot see that — the two courses'
text is nearly identical. It is a fact about time, so it is scored in Python,
never asked of an LLM (same principle as A14/A9: computed, not hallucinated).

    freshness = enrollment_multiplier * (0.55*mode_prior + 0.45*recency)

Every branch is explained in the returned `explain` dict so the card and the
README can show the breakdown rather than assert a number.
"""
from __future__ import annotations

import math
from datetime import date, datetime

# --- weights (documented in COURSE_SCHEMA.md §6; changing them changes ranking) --
W_MODE = 0.55
W_RECENCY = 0.45

# Live carries mentor access, cohort peers, and is necessarily the newest
# revision. Recorded is a replay of a finished cohort: no mentor, older content.
MODE_PRIOR = {
    "live": 1.00,
    "hybrid": 0.85,
    "self-paced": 0.60,
    "recorded": 0.45,
}
DEFAULT_MODE = "self-paced"

# Penalty, not exclusion (arch B4) — a 60-course catalog starves under hard
# exclusions, and a closed cohort still tells you what the academy teaches.
ENROLLMENT_MULTIPLIER = {
    "open": 1.00,
    "closing_soon": 1.00,   # no boost here; surfaced as `urgency` for the copy instead
    "waitlist": 0.70,
    "closed": 0.25,
}

ENROLLABLE_WINDOW_DAYS = 60   # "starts soon enough to act on" — full recency credit
FAR_COHORT_SCALE_DAYS = 240   # beyond the window, exp decay: a year out ≈ 0.47
STARTED_HALF_LIFE_DAYS = 30   # cohort already running: the seat is going, fast decay
CONTENT_HALF_LIFE_DAYS = 365  # a year-old curriculum = 0.5 in an annually-churning field
UNKNOWN_RECENCY = 0.35        # deliberately below the one-year mark — see _recency()
CLOSING_SOON_DAYS = 14


def _parse(value) -> date | None:
    """Dates arrive as ISO strings from JSON. Anything unparseable is treated as
    absent — a malformed date must never silently become a scoring input."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _future_cohort(course: dict, today: date) -> date | None:
    """format.cohort_start, but only if it is genuinely ahead of us.

    Schema §3 forbids past dates in this field, yet a cohort passes on its own
    without anyone editing the file — so the guard lives here too rather than
    trusting curation to stay current. This is the same rule that keeps a stale
    date out of generated copy.
    """
    start = _parse((course.get("format") or {}).get("cohort_start"))
    return start if start and start >= today else None


def effective_mode(course: dict, today: date | None = None) -> tuple[str, str]:
    """Returns (mode_for_scoring, reason).

    The one non-obvious rule in this module: a `live` or `hybrid` course with no
    future cohort is scored with the `recorded` prior. Not because the file is
    wrong — the course really is sold as live — but because what a buyer gets
    *today* is recordings of a cohort that already ran. Scoring it at the live
    prior would let a dead 2025 cohort outrank a genuinely upcoming one purely
    on the strength of the word "live" in its metadata.
    """
    mode = (course.get("format") or {}).get("mode") or DEFAULT_MODE
    if mode not in MODE_PRIOR:
        return DEFAULT_MODE, f"unknown mode {mode!r} — defaulted to {DEFAULT_MODE}"
    if mode in ("live", "hybrid"):
        today = today or date.today()
        if _future_cohort(course, today) is None:
            return "recorded", (f"declared {mode}, but no future cohort — scored as "
                                f"recorded (what a buyer gets today is the replay)")
    return mode, f"declared {mode}"


def _recency(course: dict, today: date) -> tuple[float, str]:
    """Score the *meaningful* date for this course's mode, not one global rule.

    For a live cohort the meaningful date is when it starts; for on-demand
    content it is when the curriculum was last revised. Applying content-age
    decay to a cohort date (or vice versa) produces confident nonsense.
    """
    fmt = course.get("format") or {}
    mode = fmt.get("mode")

    if mode in ("live", "hybrid"):
        start = _parse(fmt.get("cohort_start"))
        if start:
            days_out = (start - today).days
            if 0 <= days_out <= ENROLLABLE_WINDOW_DAYS:
                return 1.0, f"cohort starts in {days_out}d — enrollable now"
            if days_out > ENROLLABLE_WINDOW_DAYS:
                over = days_out - ENROLLABLE_WINDOW_DAYS
                return (math.exp(-over / FAR_COHORT_SCALE_DAYS),
                        f"cohort starts in {days_out}d — real but not urgent")
            # Already running: the seat is gone and value drops fast.
            return (0.5 ** (-days_out / STARTED_HALF_LIFE_DAYS),
                    f"cohort started {-days_out}d ago — {STARTED_HALF_LIFE_DAYS}d half-life")

    # Everything else decays from the most recent evidence of curriculum currency.
    for field, label in (("content_updated", "curriculum updated"),
                         ("published_at", "published")):
        ref = _parse(fmt.get(field))
        if ref:
            age = max(0, (today - ref).days)
            return (0.5 ** (age / CONTENT_HALF_LIFE_DAYS),
                    f"{label} {age}d ago — {CONTENT_HALF_LIFE_DAYS}d half-life")

    stale = _parse(course.get("cohort_start_stale"))
    if stale:
        age = max(0, (today - stale).days)
        return (0.5 ** (age / CONTENT_HALF_LIFE_DAYS),
                f"last known cohort {age}d ago — {CONTENT_HALF_LIFE_DAYS}d half-life")

    # No date anywhere. Sits below the one-year mark on purpose: unknown recency
    # must not out-rank known-fresh, and we do not invent a date to fill the gap.
    return UNKNOWN_RECENCY, "no date published — scored as unknown, not guessed"


def score_freshness(course: dict, today: date | None = None) -> dict:
    """The 0.10 fusion term, plus everything needed to justify it.

    Returns {score, mode, mode_prior, recency, enrollment_multiplier, urgency,
             label, explain} — `score` in [0, 1].
    """
    today = today or date.today()
    fmt = course.get("format") or {}

    mode, mode_reason = effective_mode(course, today)
    prior = MODE_PRIOR[mode]
    recency, recency_reason = _recency(course, today)

    status = fmt.get("enrollment_status", "open")
    multiplier = ENROLLMENT_MULTIPLIER.get(status, 1.0)

    base = W_MODE * prior + W_RECENCY * recency
    score = round(max(0.0, min(1.0, multiplier * base)), 4)

    start = _future_cohort(course, today)
    days_out = (start - today).days if start else None
    urgency = status == "closing_soon" or (days_out is not None and days_out <= CLOSING_SOON_DAYS)

    return {
        "score": score,
        "mode": mode,
        "declared_mode": fmt.get("mode"),
        "mode_prior": prior,
        "recency": round(recency, 4),
        "enrollment_status": status,
        "enrollment_multiplier": multiplier,
        "urgency": urgency,
        "label": freshness_label(course, today),
        "explain": [
            f"mode: {mode_reason} → prior {prior:.2f} (weight {W_MODE})",
            f"recency: {recency_reason} → {recency:.2f} (weight {W_RECENCY})",
            f"enrollment {status} → x{multiplier:.2f}",
            f"= {score:.3f}",
        ],
    }


def freshness_label(course: dict, today: date | None = None) -> str:
    """A short phrase the generate node may put in front of a user.

    Emits a date ONLY when that date is in the future. This is the last
    enforcement point of the stale-date rule (schema §3): even if a past cohort
    survives curation and survives the scorer, it cannot reach rendered copy
    through this function.
    """
    today = today or date.today()
    fmt = course.get("format") or {}
    declared = fmt.get("mode")

    start = _future_cohort(course, today)
    if start and declared in ("live", "hybrid"):
        days = (start - today).days
        when = "starts tomorrow" if days == 1 else (
            "starts today" if days == 0 else f"starts in {days} days")
        return f"Live cohort — {when} ({start.isoformat()})"

    if declared in ("live", "hybrid"):
        return "Recorded sessions from a previous live cohort"

    updated = _parse(fmt.get("content_updated"))
    if updated:
        months = max(0, (today - updated).days) // 30
        if months == 0:
            return "Self-paced — curriculum updated this month"
        return f"Self-paced — curriculum updated {months} month{'s' if months > 1 else ''} ago"

    return "Self-paced — start anytime"
