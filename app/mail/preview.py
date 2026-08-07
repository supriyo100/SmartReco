"""Render a message to HTML without sending it.

Deliberately duplicates a little of `messages.py`'s context-building rather
than refactoring the send functions to return their context: the send path is
the one that must not break, and threading a `dry_run` flag through four
functions to serve a debug view would put preview-only branches inside it.
Preview reuses the same builders where they already exist (`build_digest`) and
accepts the small overlap where they do not.
"""
from __future__ import annotations

from app.mail.templating import render_pair


async def render_preview(kind: str, user_id: int) -> str | None:
    """HTML for one message, or None when there is nothing to render."""
    if kind == "digest":
        from app.mail.messages import build_digest

        ctx = await build_digest(user_id)
        if ctx is None:
            return None
        return render_pair("digest", **ctx)[0]

    if kind == "welcome":
        from sqlalchemy import select

        from app.db.models import User, UserProfile
        from app.db.session import async_session
        from app.mail.messages import _first_name
        from app.profiles.routes import completeness

        async with async_session() as s:
            user = (await s.execute(
                select(User).where(User.id == user_id))).scalar_one_or_none()
            profile = (await s.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )).scalar_one_or_none()
        if user is None:
            return None
        progress = completeness(profile)
        return render_pair(
            "welcome",
            greeting_name=_first_name(profile, user.email),
            target_role=(profile.target_role if profile else "") or "",
            missing=progress["missing"][:3],
            percent=progress["percent"],
        )[0]

    # The remaining two build their context inside the send function, so
    # preview goes through a capturing stub rather than reimplementing them.
    return await _preview_via_send(kind, user_id)


async def _preview_via_send(kind: str, user_id: int) -> str | None:
    """Run the real send function with the transport swapped out.

    Monkeypatching a module attribute is normally not worth the fragility, but
    here it is the honest option: it guarantees the preview is byte-identical
    to what would be mailed, which a parallel rendering path cannot promise.
    Scoped to this call and restored in a finally.
    """
    from app.mail import messages
    from app.mail.sender import SendResult

    captured: dict[str, str] = {}

    async def _capture(to, subject, html, text):
        captured["html"] = html
        return SendResult(True, "stored", "preview")

    senders = {
        "ats": lambda: messages.send_ats_report(user_id, force=True),
        "reengage": lambda: messages.send_reengage(user_id, force=True),
    }
    if kind not in senders:
        raise ValueError(f"unknown mail kind {kind!r}")

    original = messages.send
    messages.send = _capture
    try:
        result = await senders[kind]()
    finally:
        messages.send = original

    if not result.ok or "html" not in captured:
        return None
    return captured["html"]
