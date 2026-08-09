"""User-declared profile: bio, goals, skills, and a resume.

Why this exists alongside behavioral tracking: on a brand-new account there is
no behavior. The interest model needs events it does not have yet, and the
recommendation for that person is either nothing or a popularity list — the
exact failure the project is meant to avoid. A stated background is the only
signal available at t=0, so it is worth collecting.

It never overwrites derived fields. `interests` and `fingerprint` belong to the
interest model; this router touches only the declared columns.
"""
from __future__ import annotations

import logging
import pathlib
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select, update

from app.auth.deps import require_user
from app.chat.brief import refresh_background_brief
from app.db.models import ResumeAnalysis, User, UserProfile
from app.db.session import async_session
from app.profiles.ats import ROLES, analyze, band
from app.profiles.resume import (
    ALLOWED_SUFFIXES,
    MAX_UPLOAD_BYTES,
    extract_text,
    safe_suffix,
    sanitize_filename,
)
from app.web.templating import render

log = logging.getLogger("profiles")
router = APIRouter(prefix="/profile", tags=["profile"])

RESUME_DIR = pathlib.Path("data/resumes")

# What a "complete" profile means, and what each part is worth. Weighted by
# usefulness to the recommender rather than by effort to fill in: `goals` and
# the resume are what a cold-start recommendation actually runs on, so they
# carry the most weight. Shown as a progress meter, which is the only honest
# way to ask someone for eleven fields.
COMPLETENESS_FIELDS: list[tuple[str, str, int]] = [
    ("full_name", "Your name", 8),
    ("headline", "Headline", 8),
    ("target_role", "Target role", 16),
    ("goals", "What you want next", 18),
    ("skills", "Skills", 12),
    ("bio", "About you", 10),
    ("experience_years", "Years of experience", 8),
    ("resume_text", "Resume", 20),
]


def completeness(profile: UserProfile | None) -> dict:
    """Percent complete plus the specific fields still missing.

    Returning the missing list rather than only a number is the point: "62%
    complete" tells the user nothing actionable, "add your target role and
    goals" tells them exactly what to do next.
    """
    if profile is None:
        return {"percent": 0, "missing": [label for _, label, _ in COMPLETENESS_FIELDS],
                "done": []}
    got, done, missing = 0, [], []
    for attr, label, weight in COMPLETENESS_FIELDS:
        value = getattr(profile, attr, None)
        filled = bool(value) if not isinstance(value, (list, dict)) else bool(len(value))
        if attr == "experience_years":
            filled = value is not None
        if filled:
            got += weight
            done.append(label)
        else:
            missing.append(label)
    total = sum(w for _, _, w in COMPLETENESS_FIELDS)
    return {"percent": round(100 * got / total), "missing": missing, "done": done}


def _split_skills(raw: str) -> list[str]:
    """Comma-separated → deduped list, order preserved (first mention wins)."""
    seen, out = set(), []
    for item in (raw or "").replace("\n", ",").split(","):
        s = item.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:40]


async def _get_or_create(session, user_id: int) -> UserProfile:
    profile = (await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id)
        session.add(profile)
        await session.flush()
    return profile


async def _current_ats(session, user_id: int) -> ResumeAnalysis | None:
    return (await session.execute(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user_id, ResumeAnalysis.is_current.is_(True))
        .order_by(ResumeAnalysis.created_at.desc())
    )).scalars().first()


async def _render_profile(request, user, message="", error=""):
    """One place that assembles the profile page, so every handler that lands
    on it renders the same state — a save that forgot to reload the ATS row
    would show a stale score next to fresh answers."""
    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )).scalar_one_or_none()
        ats = await _current_ats(s, user.id)
    return render(request, "profile/profile.html", p=profile, email=user.email,
                  ats=ats, ats_band=band(ats.ats_score)[1] if ats else "",
                  ats_label=band(ats.ats_score)[0] if ats else "",
                  roles=list(ROLES.keys()), progress=completeness(profile),
                  # Read off the User row, not the profile: the mail preference
                  # is an account-level fact and lives with identity (§models).
                  digest_opt_in=user.digest_opt_in,
                  message=message, error=error)


@router.get("")
@router.get("/")
async def view_profile(request: Request, user: User = Depends(require_user)):
    return await _render_profile(request, user)


@router.post("")
@router.post("/")
async def save_profile(
    request: Request,
    user: User = Depends(require_user),
    full_name: str = Form(""),
    headline: str = Form(""),
    bio: str = Form(""),
    goals: str = Form(""),
    skills: str = Form(""),
    experience_years: str = Form(""),
    target_role: str = Form(""),
    current_role: str = Form(""),
    location: str = Form(""),
    phone: str = Form(""),
    linkedin_url: str = Form(""),
    github_url: str = Form(""),
    education: str = Form(""),
    budget_max: str = Form(""),
    weekly_hours: str = Form(""),
    preferred_mode: str = Form(""),
    resume_text: str = Form(""),
    form: str = Form(""),
    resume: UploadFile | None = File(None),
):
    warning = ""
    async with async_session() as s:
        profile = await _get_or_create(s, user.id)

        # The full edit form posts `form=full` (a hidden field), which means
        # "these values are the complete truth" — so a cleared box erases the
        # stored value, as the user expects. Any other submit is treated as
        # partial and leaves unsent fields alone, so a targeted action can't
        # silently wipe a bio the user never touched.
        full_submit = form == "full"

        def keep(new: str, current: str, limit: int) -> str:
            new = (new or "").strip()[:limit]
            return new if (new or full_submit) else current

        profile.full_name = keep(full_name, profile.full_name, 200)
        profile.headline = keep(headline, profile.headline, 200)
        profile.bio = keep(bio, profile.bio, 5000)
        profile.goals = keep(goals, profile.goals, 2000)
        profile.target_role = keep(target_role, profile.target_role, 120)
        profile.current_role = keep(current_role, profile.current_role, 120)
        profile.location = keep(location, profile.location, 120)
        profile.phone = keep(phone, profile.phone, 40)
        profile.linkedin_url = keep(linkedin_url, profile.linkedin_url, 300)
        profile.github_url = keep(github_url, profile.github_url, 300)
        profile.education = keep(education, profile.education, 300)
        profile.preferred_mode = keep(preferred_mode, profile.preferred_mode, 40)
        parsed_skills = _split_skills(skills)
        if parsed_skills or full_submit:
            profile.skills = parsed_skills

        # Budget and hours follow the same "blank means not stated, never
        # zero" rule as experience_years: a silent 0 budget would filter the
        # entire paid catalog out of every recommendation.
        raw_budget = (budget_max or "").strip().replace(",", "")
        if raw_budget:
            try:
                value = float(raw_budget)
                if 0 <= value <= 10_000_000:
                    profile.budget_max = value
            except ValueError:
                pass
        elif full_submit:
            profile.budget_max = None

        raw_hours = (weekly_hours or "").strip()
        if raw_hours.isdigit() and 0 <= int(raw_hours) <= 168:
            profile.weekly_hours = int(raw_hours)
        elif not raw_hours and full_submit:
            profile.weekly_hours = None

        # A blank box means "not stated", not zero. Anything unparseable is
        # dropped rather than coerced — a silent 0 would read as "no experience".
        years = (experience_years or "").strip()
        if years.isdigit() and 0 <= int(years) <= 60:
            profile.experience_years = int(years)
        elif not years and full_submit:
            profile.experience_years = None

        # Pasted text is authoritative when present: the user can always correct
        # a bad extraction by typing over it, and losing that edit on the next
        # save would make the paste box useless.
        pasted = (resume_text or "").strip()
        if pasted or (full_submit and not (resume is not None and resume.filename)):
            profile.resume_text = pasted[:20_000]

        if resume is not None and resume.filename:
            suffix = safe_suffix(resume.filename)
            raw = await resume.read()
            if len(raw) > MAX_UPLOAD_BYTES:
                warning = (f"{resume.filename} is larger than "
                           f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB — not saved.")
            elif suffix not in ALLOWED_SUFFIXES:
                warning = (f"{suffix or 'that file type'} is not accepted "
                           f"(.txt, .md, .pdf, .docx).")
            else:
                RESUME_DIR.mkdir(parents=True, exist_ok=True)
                # Name the file by user id, not by the uploaded name: it makes
                # collisions impossible and re-uploads overwrite cleanly.
                stored = RESUME_DIR / f"user_{user.id}{suffix}"
                stored.write_bytes(raw)
                profile.resume_filename = sanitize_filename(resume.filename)
                profile.resume_path = str(stored).replace("\\", "/")
                profile.resume_uploaded_at = datetime.utcnow()
                text, warning = extract_text(raw, resume.filename)
                if text and not pasted:
                    profile.resume_text = text

        # The ATS run is what the user came for, so it happens on save rather
        # than behind a second button they might never press. It is pure
        # Python (no model call), so doing it inline costs microseconds — the
        # efficiency argument in §5.1 is about LLM calls, and this is not one.
        resume_now = (profile.resume_text or "").strip()
        role_now = profile.target_role or ""
        if resume_now:
            await _store_analysis(s, user.id, resume_now, role_now)

        # Re-render the chat prompt brief now, at write-time, so the next
        # chat turn reads a stored block instead of falling back to a live
        # render — see app/chat/brief.py / plan.md §1-5.
        await refresh_background_brief(s, user.id)

        await s.commit()

    # Someone who just stated a target role or uploaded a resume has given us
    # the strongest signal we will ever get, and the recommendations page was
    # showing them nothing. Fired and not awaited: generation takes seconds and
    # must not sit on a form POST.
    _kick_recommendations(user.id, "profile_change")

    message = "" if warning else "Profile saved."
    return await _render_profile(request, user, message=message, error=warning)


def _kick_recommendations(user_id: int, reason: str) -> None:
    """Ask the planner to consider a run, in the background.

    `maybe_generate` consults the trigger policy first, so this is safe to call
    on every save — a user editing five fields in a row produces one run, not
    five, because of the debounce in app/agent/triggers.py.
    """
    import asyncio

    async def _run() -> None:
        from app.agent.graph import maybe_generate

        try:
            await maybe_generate(user_id, reason_hint=reason)
        except Exception:
            log.exception("recommendation trigger failed for user_id=%s", user_id)

    asyncio.create_task(_run())


async def _store_analysis(session, user_id: int, resume_text: str,
                          target_role: str) -> ResumeAnalysis:
    """Run the ATS and store the result as the new current row.

    Previous runs are demoted rather than deleted — the same `is_current`
    pattern `recommendations` uses. History is what lets a user see that
    editing their resume moved the score, which is the only way the number
    means anything to them.
    """
    result = analyze(resume_text, target_role)
    await session.execute(
        update(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user_id, ResumeAnalysis.is_current.is_(True))
        .values(is_current=False)
    )
    row = ResumeAnalysis(user_id=user_id, is_current=True, **result)
    session.add(row)
    await session.flush()
    return row


@router.post("/ats")
async def run_ats(request: Request, user: User = Depends(require_user),
                  target_role: str = Form("")):
    """Re-score against a (possibly different) target role.

    Scoring the same resume against a new role is the common case — people
    apply to more than one — and it must not require re-uploading the file.
    """
    async with async_session() as s:
        profile = await _get_or_create(s, user.id)
        role = (target_role or "").strip()[:120]
        if role:
            profile.target_role = role
        text = (profile.resume_text or "").strip()
        if not text:
            await s.commit()
            return await _render_profile(
                request, user,
                error="No resume text yet — upload a file or paste the text first.")
        await _store_analysis(s, user.id, text, profile.target_role or "")
        await refresh_background_brief(s, user.id)
        await s.commit()
    # A new gap list is a new basis for recommending — see §5.2 `ats_run`.
    _kick_recommendations(user.id, "ats_run")
    return await _render_profile(request, user, message="Resume re-analyzed.")


@router.post("/email-prefs")
async def save_email_prefs(request: Request, user: User = Depends(require_user),
                           digest_opt_in: str = Form("")):
    """The unsubscribe target. Every automated email links here.

    An unchecked box means off — this form always posts the complete truth
    about one boolean, so the usual "blank means untouched" rule from
    `save_profile` does not apply and must not: it would make unsubscribing
    impossible, since unchecking is exactly the case that sends nothing.
    """
    opted_in = bool(digest_opt_in)
    async with async_session() as s:
        await s.execute(update(User).where(User.id == user.id)
                        .values(digest_opt_in=opted_in))
        await s.commit()
    # The dependency-injected `user` is a snapshot from before this write, so
    # the page would otherwise re-render with the old checkbox state.
    user.digest_opt_in = opted_in
    return await _render_profile(
        request, user,
        message=("You'll receive the daily digest." if opted_in
                 else "Unsubscribed — no more automated email."))


@router.post("/ats/email")
async def email_ats_report(request: Request, user: User = Depends(require_user)):
    """Mail the current analysis to the account's own address.

    Behind a button rather than fired automatically on every ATS run, which is
    the difference between a useful feature and spam: `save_profile` re-scores
    on every save, and mailing a report each time someone fixes a typo in their
    bio would get this sender filtered within a week. The user asks, once.

    `force=True` because pressing this button is a direct request — the
    `digest_opt_in` preference governs the unsolicited daily mail, not a report
    the user just asked for by name.
    """
    from app.mail.messages import send_ats_report

    try:
        result = await send_ats_report(user.id, force=True)
    except Exception as exc:
        return await _render_profile(
            request, user, error=f"Could not send: {type(exc).__name__}: {exc}")

    if result.mode == "skipped":
        return await _render_profile(
            request, user, error="Run an analysis first — there's no report to send yet.")
    if not result.ok:
        return await _render_profile(request, user,
                                     error=f"Could not send: {result.detail}")
    if result.mode == "stored":
        # Told plainly rather than claiming a delivery that did not happen.
        return await _render_profile(
            request, user,
            message=("Email is not configured on this server, so the report was saved "
                     "to disk instead of sent."))
    return await _render_profile(request, user,
                                 message=f"Report sent to {user.email}.")


@router.get("/resume")
async def download_resume(user: User = Depends(require_user)):
    """Serve the user their OWN resume. The path comes from their row, never
    from the request, so there is no traversal surface here."""
    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )).scalar_one_or_none()
    if profile is None or not profile.resume_path:
        return RedirectResponse("/profile", status_code=303)
    path = pathlib.Path(profile.resume_path)
    if not path.is_file():
        return RedirectResponse("/profile", status_code=303)
    return FileResponse(path, filename=profile.resume_filename or path.name)


@router.post("/resume/delete")
async def delete_resume(user: User = Depends(require_user)):
    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )).scalar_one_or_none()
        if profile is not None:
            if profile.resume_path:
                pathlib.Path(profile.resume_path).unlink(missing_ok=True)
            profile.resume_filename = ""
            profile.resume_path = ""
            profile.resume_text = ""
            profile.resume_uploaded_at = None
            # Retire the score with the resume it was computed from. Leaving it
            # current would show a user an ATS result for a document that no
            # longer exists — the rows are kept, just no longer surfaced.
            await s.execute(
                update(ResumeAnalysis)
                .where(ResumeAnalysis.user_id == user.id,
                       ResumeAnalysis.is_current.is_(True))
                .values(is_current=False)
            )
            await s.commit()
    return RedirectResponse("/profile", status_code=303)
