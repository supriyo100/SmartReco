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

import pathlib
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from app.auth.deps import require_user
from app.db.models import User, UserProfile
from app.db.session import async_session
from app.profiles.resume import (ALLOWED_SUFFIXES, MAX_UPLOAD_BYTES,
                                 extract_text, safe_suffix, sanitize_filename)
from app.web.templating import render

router = APIRouter(prefix="/profile", tags=["profile"])

RESUME_DIR = pathlib.Path("data/resumes")


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


@router.get("")
@router.get("/")
async def view_profile(request: Request, user: User = Depends(require_user)):
    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )).scalar_one_or_none()
    return render(request, "profile/profile.html", p=profile, email=user.email)


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
        parsed_skills = _split_skills(skills)
        if parsed_skills or full_submit:
            profile.skills = parsed_skills

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

        await s.commit()

    message = "" if warning else "Profile saved."
    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )).scalar_one_or_none()
    return render(request, "profile/profile.html", p=profile, email=user.email,
                  message=message, error=warning)


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
            await s.commit()
    return RedirectResponse("/profile", status_code=303)
