"""Trigger policy — arch §5.2. THIS IS THE PLANNER.

The project's central claim: the expensive layer runs only when cheap
deterministic code has established that it is worth running. This module is
that code. Nothing here calls a model — a planner that spends tokens deciding
whether to spend tokens has given up the argument.

A run is justified when any of these is true:

  profile_change   the user edited their target role, uploaded a resume, or
                   re-ran the ATS. Their stated intent changed, so a set built
                   on the old one is answering the wrong question.
  fingerprint_move behavioral interest moved past FINGERPRINT_COS_THRESHOLD.
  enough_events    TRIGGER_MIN_EVENTS accumulated since the last run, even if
                   the fingerprint held — sustained reading in one area is
                   signal that a coarse fingerprint can miss.
  chat_facts       the conversation revealed a budget, role or topic the
                   stored set did not account for.
  stale            the set is older than REC_STALE_HOURS and the user is back.

And suppressed when:

  debounce         a run happened within TRIGGER_DEBOUNCE_S. Someone editing
                   their profile field by field would otherwise fire five runs.
  cold_start_floor fewer than COLD_START_MIN_EVENTS and no declared profile.
                   With nothing to go on, a generated set is a popularity list
                   with an LLM caption — the exact failure this project exists
                   to avoid. Better to show the empty-state copy that tells
                   them what to do.
  in_flight        a per-user lock, so two triggers cannot generate twice
                   concurrently and race on `is_current`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.db.models import Event, Recommendation, ResumeAnalysis, UserProfile
from app.db.session import async_session

log = logging.getLogger("agent.triggers")

# One lock per user. Held only for the duration of a generation, and created
# lazily so a large user base does not pre-allocate thousands of locks.
_locks: dict[int, asyncio.Lock] = {}


def lock_for(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = _locks[user_id] = asyncio.Lock()
    return lock


def is_running(user_id: int) -> bool:
    lock = _locks.get(user_id)
    return bool(lock and lock.locked())


async def should_run(user_id: int, reason_hint: str = "") -> tuple[bool, str, dict]:
    """Decide whether to generate. Returns (run, reason, diagnostics).

    `reason_hint` is what the caller believes happened ("profile_change",
    "chat_facts"). It is trusted as an input but still checked against the
    suppression rules — a caller that fires on every keystroke must not be able
    to bypass the debounce.
    """
    now = datetime.utcnow()
    diagnostics: dict = {"hint": reason_hint}

    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )).scalar_one_or_none()
        current = (await s.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user_id,
                   Recommendation.is_current.is_(True))
            .order_by(Recommendation.created_at.desc())
        )).scalars().first()
        event_count = (await s.execute(
            select(func.count(Event.id)).where(Event.user_id == user_id)
        )).scalar() or 0
        ats = (await s.execute(
            select(ResumeAnalysis)
            .where(ResumeAnalysis.user_id == user_id,
                   ResumeAnalysis.is_current.is_(True))
        )).scalars().first()

    has_declared = bool(profile and (profile.target_role or profile.goals
                                     or profile.resume_text or profile.skills))
    diagnostics.update({"events": event_count, "has_declared": has_declared,
                        "has_current": current is not None})

    # --- suppression, checked first -------------------------------------
    if is_running(user_id):
        return False, "in_flight", diagnostics

    if current is not None:
        age = (now - current.created_at).total_seconds()
        diagnostics["age_s"] = int(age)
        if age < settings.TRIGGER_DEBOUNCE_S:
            return False, "debounce", diagnostics

    # The cold-start floor. A declared profile counts as signal on its own —
    # someone who typed a target role and uploaded a resume has told us more
    # than eight page views would, so they should not have to browse first.
    # That is precisely the case that was producing an empty page.
    if not has_declared and event_count < settings.COLD_START_MIN_EVENTS:
        return False, "cold_start_floor", diagnostics

    # --- reasons to run --------------------------------------------------
    if current is None:
        return True, "first_run", diagnostics

    if reason_hint in ("profile_change", "resume_upload", "ats_run", "chat_facts"):
        return True, reason_hint, diagnostics

    # Behavioral movement: has the fingerprint drifted past the threshold?
    if profile and profile.fingerprint and current.fingerprint:
        if profile.fingerprint != current.fingerprint:
            from app.agent.interests import refresh_interests

            report = await refresh_interests(user_id)
            distance = report["cos_dist"]
            diagnostics["cos_dist"] = round(distance, 4)
            if distance >= settings.FINGERPRINT_COS_THRESHOLD:
                return True, "fingerprint_move", diagnostics

    # Sustained reading that a coarse fingerprint missed.
    async with async_session() as s:
        since_last = (await s.execute(
            select(func.count(Event.id)).where(
                Event.user_id == user_id, Event.ts >= current.created_at)
        )).scalar() or 0
    diagnostics["events_since"] = since_last
    if since_last >= settings.TRIGGER_MIN_EVENTS:
        return True, "enough_events", diagnostics

    # A resume analysed after the current set was built.
    if ats is not None and ats.created_at > current.created_at:
        return True, "ats_run", diagnostics

    # Staleness, but only for someone actually here to see it. Refreshing sets
    # for users who left is work nobody reads.
    stale_after = current.created_at + timedelta(hours=settings.REC_STALE_HOURS)
    if now >= stale_after and since_last > 0:
        return True, "stale", diagnostics

    return False, "no_trigger", diagnostics
