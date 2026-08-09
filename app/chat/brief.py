"""The stored "WHAT YOU KNOW ABOUT THIS USER" chat prompt block.

`app/chat/agent.py:build_user_context()` used to re-derive this text from
`UserProfile` + `ResumeAnalysis` + `Recommendation` on every single chat
turn — three queries and a string assembly, paid again whether or not any of
those rows had changed since the last message. See plan.md §1-5 for the
measurement (2,150 prompt tokens on a thin profile) that motivated this.

The fix is the same shape `app/chat/context.py` already uses for
conversation history: render deterministically (no LLM call — a model asked
to summarize "budget is 5000" tends to destroy the number that made it
useful), once, whenever the source data actually changes, and store the
result. `build_user_context()` then reads it; the three write-time callers
below (profile save, a new ATS run, a new recommendation) call
`refresh_background_brief()` so a read is normally a cache hit, not a miss.

`_fingerprint()` is the invalidation key: it hashes only the fields
`render_background_brief()` actually reads, so a field that never appears in
the brief (e.g. `preferred_mode` alone) changing does not force a
re-render — the same "regenerate on every relevant change, not every save"
discipline `Recommendation.fingerprint` already uses.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select

from app.db.models import Recommendation, ResumeAnalysis, UserProfile


def _fingerprint(profile: UserProfile | None, ats: ResumeAnalysis | None,
                 rec: Recommendation | None) -> str:
    parts: list[str] = []
    if profile:
        top_interests = sorted(
            (profile.interests or {}).items(), key=lambda kv: -float(kv[1] or 0))[:5]
        parts += [
            profile.full_name, profile.headline, profile.current_role,
            profile.target_role, str(profile.experience_years), profile.goals,
            ",".join(list(profile.skills or [])[:25]), str(profile.budget_max),
            str(profile.weekly_hours), profile.preferred_mode,
            str(top_interests), "has_resume" if profile.resume_text else "",
        ]
    if ats:
        parts += [str(ats.id), str(ats.ats_score), ats.target_role,
                 ",".join(list(ats.missing_skills or [])[:10]),
                 ",".join(list(ats.matched_skills or [])[:12])]
    if rec:
        parts += [str(rec.id), (rec.narrative or "")[:300],
                 str([(i.get("product_id"), i.get("rank"), i.get("hook"))
                     for i in sorted(rec.items or [], key=lambda i: i.get("rank", 0))[:4]])]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def render_background_brief(profile: UserProfile | None, ats: ResumeAnalysis | None,
                            rec: Recommendation | None) -> str:
    """Pure function: profile/ats/rec rows → the prompt text. No DB, no LLM.

    Extracted from `build_user_context()` with no behavior change — same
    field selection, same truncation lengths, same ordering.
    """
    lines: list[str] = []

    if profile:
        if profile.full_name:
            lines.append(f"Name: {profile.full_name}")
        if profile.headline:
            lines.append(f"Headline: {profile.headline}")
        if profile.current_role:
            lines.append(f"Current role: {profile.current_role}")
        if profile.target_role:
            lines.append(f"TARGET ROLE: {profile.target_role}")
        if profile.experience_years is not None:
            lines.append(f"Experience: {profile.experience_years} years")
        if profile.goals:
            lines.append(f"Stated goal: {profile.goals[:400]}")
        if profile.skills:
            lines.append(f"Skills they claim: {', '.join(list(profile.skills)[:25])}")
        if profile.budget_max is not None:
            lines.append(f"BUDGET: at most ₹{int(profile.budget_max):,}")
        if profile.weekly_hours:
            lines.append(f"Time available: ~{profile.weekly_hours} h/week")
        if profile.preferred_mode:
            lines.append(f"Prefers: {profile.preferred_mode} courses")
        if profile.interests:
            top = sorted(profile.interests.items(), key=lambda kv: -float(kv[1] or 0))[:5]
            if top:
                lines.append("Browsing shows interest in: " +
                             ", ".join(f"{k}" for k, _ in top))

    if ats:
        lines.append(f"RESUME ATS SCORE: {ats.ats_score}/100 against "
                     f"'{ats.target_role}'")
        if ats.missing_skills:
            lines.append("RESUME GAPS (missing for that role): " +
                         ", ".join(list(ats.missing_skills)[:10]))
        if ats.matched_skills:
            lines.append("Already evidenced on resume: " +
                         ", ".join(list(ats.matched_skills)[:12]))
    elif profile and not (profile.resume_text or ""):
        lines.append("No resume uploaded yet — suggest it once if relevant, "
                     "then drop it.")

    if rec and rec.narrative:
        lines.append(f"Their current recommendation summary: {rec.narrative[:300]}")
    if rec and rec.items:
        for item in sorted(rec.items, key=lambda i: i.get("rank", 0))[:4]:
            terms = item.get("terms") or {}
            why = []
            if terms.get("gap_match", 0) > 0:
                why.append("closes resume gaps")
            if terms.get("interest_match", 0) > 0.15:
                why.append("matches what they browse")
            if terms.get("graph_adjacency", 0) >= 1.0:
                why.append("next step after a course they viewed")
            if terms.get("level_fit", 0) >= 1.0:
                why.append("right level for their experience")
            lines.append(
                f"ALREADY RECOMMENDED id:{item.get('product_id')} "
                f"(rank {item.get('rank')}, confidence "
                f"{item.get('confidence')}): {item.get('hook', '')} "
                + (f"[scored because: {', '.join(why)}]" if why else ""))

    return ("\n".join(f"- {line}" for line in lines)
            or "- Nothing known about this user yet (new account, no resume, "
               "no browsing history). Ask one short question to orient.")


async def _load(session, user_id: int):
    profile = (await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )).scalar_one_or_none()
    ats = (await session.execute(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user_id, ResumeAnalysis.is_current.is_(True))
        .order_by(ResumeAnalysis.created_at.desc())
    )).scalars().first()
    rec = (await session.execute(
        select(Recommendation)
        .where(Recommendation.user_id == user_id, Recommendation.is_current.is_(True))
        .order_by(Recommendation.created_at.desc())
    )).scalars().first()
    return profile, ats, rec


async def refresh_background_brief(session, user_id: int) -> str:
    """(Re)render the brief and store it on the profile row, in `session`.

    Call this after any write that changes what the brief reads — a profile
    save, a new ATS run, a new recommendation — so the chat agent's next
    read is a cache hit. Does not commit; the caller's existing transaction
    does. No-op (returns "") if the user has no profile row yet.
    """
    profile, ats, rec = await _load(session, user_id)
    if profile is None:
        return ""
    brief = render_background_brief(profile, ats, rec)
    profile.background_brief = brief
    profile.brief_fingerprint = _fingerprint(profile, ats, rec)
    return brief
