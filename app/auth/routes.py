"""Register / login / logout.

Registration collects email, password, and a few optional profile fields. The
optional half is a deliberate revision of the original "email + password,
nothing else" rule (arch §1.1), for a reason the tracking design already
implies: a new account has no behavior, so the first recommendation has nothing
to run on but what the person told us. Asking for a name and a target role at
the one moment someone is already filling in a form is far cheaper than hoping
they visit /profile later.

They stay OPTIONAL, and that is the other half of the decision. A required
six-field signup is an abandonment funnel, and the fields are all editable at
/profile afterwards — so the cost of skipping them is zero and the cost of
requiring them is real.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from app.auth.security import (
    COOKIE_NAME,
    SID_COOKIE,
    cookie_kwargs,
    hash_password,
    sign_session,
    verify_password,
)
from app.db.models import Event, User, UserProfile
from app.db.session import async_session
from app.profiles.ats import ROLES
from app.web.templating import render

log = logging.getLogger("auth")
router = APIRouter(prefix="/auth", tags=["auth"])


async def _stitch_session_events(session_id: str, user_id: int) -> int:
    """§4.2 identity stitching: backfill user_id onto this browser's anonymous
    events, so a first recommendation can draw on what they did before signing
    up. Scoped to rows still NULL — never re-attributes another user's events.
    """
    if not session_id:
        return 0
    async with async_session() as s:
        res = await s.execute(
            update(Event)
            .where(Event.session_id == session_id, Event.user_id.is_(None))
            .values(user_id=user_id)
        )
        await s.commit()
        return res.rowcount or 0


def _login_response(request: Request, user: User, to: str = "/") -> RedirectResponse:
    resp = RedirectResponse(to, status_code=303)
    resp.set_cookie(COOKIE_NAME, sign_session(user.id, user.role),
                    **cookie_kwargs(request))
    return resp


@router.get("/register")
async def register_form(request: Request):
    return render(request, "auth/register.html", roles=list(ROLES.keys()))


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    target_role: str = Form(""),
    experience_years: str = Form(""),
    goals: str = Form(""),
    digest_opt_in: str = Form(""),
):
    email = email.strip().lower()
    # Everything typed is echoed back on an error, so a failed password rule
    # does not make someone retype the four fields they got right.
    typed = {"email": email, "full_name": full_name, "target_role": target_role,
             "experience_years": experience_years, "goals": goals,
             "roles": list(ROLES.keys())}

    if len(password) < 8:
        return render(request, "auth/register.html",
                      error="Password must be at least 8 characters.", **typed)
    try:
        pw_hash = hash_password(password)
    except ValueError as exc:
        return render(request, "auth/register.html", error=str(exc), **typed)

    async with async_session() as s:
        exists = (await s.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
        if exists:
            return render(request, "auth/register.html",
                          error="That email is already registered.", **typed)
        user = User(email=email, password_hash=pw_hash, role="user",
                    digest_opt_in=bool(digest_opt_in))
        s.add(user)
        await s.flush()          # need user.id for the profile row

        # The profile row is created here rather than lazily on first visit to
        # /profile, so the recommender never has to special-case its absence.
        # Only DECLARED columns are written — the derived half belongs to the
        # interest model (§1.2) and must not be initialized by this path.
        years: int | None = None
        raw_years = (experience_years or "").strip()
        if raw_years.isdigit() and 0 <= int(raw_years) <= 60:
            years = int(raw_years)

        s.add(UserProfile(
            user_id=user.id,
            full_name=(full_name or "").strip()[:200],
            target_role=(target_role or "").strip()[:120],
            goals=(goals or "").strip()[:2000],
            experience_years=years,
        ))
        await s.commit()
        await s.refresh(user)

    n = await _stitch_session_events(getattr(request.state, "session_id", ""), user.id)
    log.info("registered user_id=%s stitched_events=%d", user.id, n)

    # Welcome mail, fired and not awaited. Registration must not wait on an
    # SMTP handshake — that is a network round-trip to Gmail on the critical
    # path of a form post, and a slow or dead mail server would turn a working
    # signup into a timeout. The task logs its own failure; the account exists
    # either way, which is the correct trade.
    asyncio.create_task(_welcome_mail(user.id))

    # Straight to the profile page: someone who just told us their target role
    # is exactly the person who will upload a resume if asked now.
    return _login_response(request, user, to="/profile")


async def _welcome_mail(user_id: int) -> None:
    """Background welcome send. Never propagates — nothing awaits it."""
    from app.mail.messages import send_welcome

    try:
        result = await send_welcome(user_id)
        if not result.ok:
            log.warning("welcome mail for user_id=%s failed: %s", user_id, result.detail)
    except Exception:
        log.exception("welcome mail for user_id=%s raised", user_id)


@router.get("/login")
async def login_form(request: Request):
    return render(request, "auth/login.html")


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    async with async_session() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # One message for both branches — telling an attacker which half was wrong
    # turns the login form into an account-enumeration oracle.
    if user is None or not verify_password(password, user.password_hash):
        return render(request, "auth/login.html", email=email,
                      error="Incorrect email or password.")

    async with async_session() as s:
        await s.execute(update(User).where(User.id == user.id)
                        .values(last_login_at=datetime.utcnow()))
        await s.commit()

    n = await _stitch_session_events(getattr(request.state, "session_id", ""), user.id)
    log.info("login user_id=%s stitched_events=%d", user.id, n)
    return _login_response(request, user)


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    # Drop the tracking sid too, so a shared browser does not stitch the next
    # person's anonymous browsing onto this account at their next login.
    resp.delete_cookie(SID_COOKIE, path="/")
    return resp
