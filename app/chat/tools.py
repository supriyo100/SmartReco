"""Tools the chat agent (app/chat/agent.py) can call.

Each tool receives the caller's identity via `ToolRuntime.context` rather than
a closure over `user_id` — `create_agent` builds the agent graph once at
import time (so the middleware stack, checkpointer and tool list are shared
across requests), and `context=ChatContext(user_id=...)` is what scopes a
single invocation to one user without rebuilding any of that per request.

`search_catalog` and `get_course_details` format results with
`app/chat/agent.py:_fmt_course()` — the same `id:N | Title | ...` shape the
old single-shot prompt used — so the grounding and offline-fallback
middleware (app/chat/agent_middleware.py) can parse course ids out of tool
output with one shared regex instead of each surface inventing its own.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain.tools import ToolRuntime, tool
from sqlalchemy import select

from app.chat.retrieval import retrieve
from app.config import settings
from app.db.models import Product, UserProfile
from app.db.session import async_session

log = logging.getLogger("chat.tools")

RETRIEVE_K = 6


@dataclass
class ChatContext:
    """Runtime context for one chat turn — the per-request identity that
    would otherwise need a closure over every tool.

    `knowledge` is the profile/resume/history-derived system-prompt block
    `app/chat/agent.py:build_user_context()` already assembles per turn;
    carried here so the dynamic-prompt middleware (agent_middleware.py) can
    read it without a second DB round trip.
    """
    user_id: int
    knowledge: str = ""
    conversation_id: int = 0


@tool
async def search_catalog(query: str, runtime: ToolRuntime[ChatContext],
                         max_price: float | None = None,
                         level: str | None = None) -> str:
    """Search the course catalog. Returns up to 6 matching courses.

    Call this when the learner asks about a topic, skill or role and you do
    not already have matching courses from earlier in this turn. `max_price`
    filters to courses at or below that price (in rupees). `level` narrows to
    'beginner', 'intermediate' or 'advanced' when the learner has said which
    they want.
    """
    from app.chat.agent import _fmt_course

    courses, _path = await retrieve(query, top_k=RETRIEVE_K, max_price=max_price,
                                    rerank_query=query, level=level)
    if not courses:
        return "No courses matched that search."
    return "\n".join(_fmt_course(p) for p in courses)


@tool
async def get_course_details(course_id: int, runtime: ToolRuntime[ChatContext]) -> str:
    """Get full details for one course by id, when you need more than the
    search result gave you — e.g. the learner asks what a course actually
    covers before you recommend it."""
    from app.chat.agent import _fmt_course

    async with async_session() as s:
        product = (await s.execute(
            select(Product).where(Product.id == course_id, Product.is_active.is_(True))
        )).scalar_one_or_none()
    if product is None:
        return f"No active course with id {course_id}."
    return _fmt_course(product)


@tool
async def update_learner_profile(runtime: ToolRuntime[ChatContext],
                                 target_role: str | None = None,
                                 budget_max: float | None = None,
                                 weekly_hours: int | None = None,
                                 current_role: str | None = None) -> str:
    """Save a profile fact the learner just stated in conversation — their
    target role, budget ceiling, weekly hours available, or current role.
    Only pass the field(s) they actually stated; leave the rest unset. This
    pauses for the learner's confirmation before it takes effect."""
    user_id = runtime.context.user_id
    fields = {k: v for k, v in {
        "target_role": target_role, "budget_max": budget_max,
        "weekly_hours": weekly_hours, "current_role": current_role,
    }.items() if v is not None}
    if not fields:
        return "Nothing to update."

    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(user_id=user_id)
            s.add(profile)
        for k, v in fields.items():
            setattr(profile, k, v)
        await s.commit()

    return f"Updated: {', '.join(f'{k}={v}' for k, v in fields.items())}."


@tool
async def refresh_recommendations(reason: str, runtime: ToolRuntime[ChatContext]) -> str:
    """Trigger a recommendation refresh when the conversation revealed a
    durable preference (a new target role, budget, or goal) the stored
    recommendation set does not account for yet. `reason` is a short phrase
    for why (e.g. 'target role changed to ML Engineer')."""
    from app.agent.graph import maybe_generate

    if settings.ENV == "test":
        return "Skipped in test."
    user_id = runtime.context.user_id
    report = await maybe_generate(user_id, reason_hint=reason)
    if not report.get("ran"):
        return f"Not refreshed ({report.get('reason', 'no_trigger')})."
    if not report.get("stored"):
        return "Refresh ran but produced nothing new."
    return f"Recommendations refreshed: {report.get('items', 0)} items."


CHAT_TOOLS = [search_catalog, get_course_details, update_learner_profile,
             refresh_recommendations]
