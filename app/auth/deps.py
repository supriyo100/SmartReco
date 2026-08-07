"""Request-scoped identity: middleware + the two FastAPI dependencies.

`require_admin` is applied once, to the admin router (arch §1.1) — not to each
handler, which is how one handler eventually gets missed.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from app.auth.security import COOKIE_NAME, SID_COOKIE, cookie_kwargs, read_session
from app.db.models import User
from app.db.session import async_session


async def identity_middleware(request: Request, call_next):
    """Populate request.state.{user_id,role,session_id} for every request.

    The tracker's /api/events handler already reads request.state.user_id; this
    is what fills it. Anonymous visitors get a persistent `sid` cookie so their
    events can be stitched to a user_id at login (§4.2).
    """
    sess = read_session(request.cookies.get(COOKIE_NAME))
    request.state.user_id = sess["uid"] if sess else None
    # Role from the DB, not the cookie: a cookie signed before a promotion or
    # demotion carries the old role for up to MAX_AGE_S. One indexed PK lookup
    # per authenticated request is the right price for not serving a stale role.
    request.state.role = None
    request.state.email = None
    if request.state.user_id is not None:
        async with async_session() as s:
            # Both columns in the one lookup this already performed: the
            # sidebar shows the signed-in address on every page, and a second
            # query per request to fetch it would be pure waste.
            row = (await s.execute(
                select(User.role, User.email).where(User.id == request.state.user_id)
            )).first()
        if row is None:
            # User deleted since the cookie was signed — treat as anonymous.
            request.state.user_id = None
        else:
            request.state.role, request.state.email = row

    sid = request.cookies.get(SID_COOKIE)
    new_sid = None
    if not sid:
        sid = new_sid = uuid.uuid4().hex
    request.state.session_id = sid

    response = await call_next(request)
    if new_sid:
        # sid is readable by tracker.js, so no httponly here.
        kw = cookie_kwargs(request)
        kw["httponly"] = False
        response.set_cookie(SID_COOKIE, new_sid, **kw)
    return response


async def current_user(request: Request) -> User | None:
    """The logged-in User row, or None. Re-reads the DB so a deleted or
    demoted user cannot keep acting on a cookie signed before the change."""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return None
    async with async_session() as s:
        return (await s.execute(select(User).where(User.id == uid))).scalar_one_or_none()


async def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")
    return user


async def require_admin(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
