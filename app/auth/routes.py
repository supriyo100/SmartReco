"""Register / login / logout. Email + password, nothing else (arch §1.1)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from app.auth.security import (COOKIE_NAME, SID_COOKIE, cookie_kwargs,
                               hash_password, sign_session, verify_password)
from app.db.models import Event, User
from app.db.session import async_session
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
    return render(request, "auth/register.html")


@router.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if len(password) < 8:
        return render(request, "auth/register.html", email=email,
                      error="Password must be at least 8 characters.")
    try:
        pw_hash = hash_password(password)
    except ValueError as exc:
        return render(request, "auth/register.html", email=email, error=str(exc))

    async with async_session() as s:
        exists = (await s.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
        if exists:
            return render(request, "auth/register.html", email=email,
                          error="That email is already registered.")
        user = User(email=email, password_hash=pw_hash, role="user")
        s.add(user)
        await s.commit()
        await s.refresh(user)

    n = await _stitch_session_events(getattr(request.state, "session_id", ""), user.id)
    log.info("registered user_id=%s stitched_events=%d", user.id, n)
    return _login_response(request, user)


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
