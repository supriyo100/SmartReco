"""Usage guardrails for the chat agent: session, per-user and platform-wide
token budgets, enforced from `llm_call_log` (app/agent/telemetry.py) before a
turn spends anything.

Mesh ran out of balance mid-build (402 spend_limit_exceeded — see
app/agent/providers.py) with no warning, and the first symptom was every
chat turn silently failing. This is the check that turns that into "you've
hit today's limit" instead of an outage: it runs first in `answer()`
(app/chat/agent.py), before retrieval or any model call, so a blocked turn
costs nothing at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.db.models import LLMCallLog
from app.db.session import async_session


@dataclass
class BudgetCheck:
    allowed: bool
    reason: str = ""


async def check_budget(user_id: int, conversation_id: int) -> BudgetCheck:
    """Three scopes, tightest-first: a runaway thread should not need a
    platform-wide outage to be noticed, and a platform-wide cap should not
    require every user to have individually misbehaved."""
    since_day = datetime.utcnow() - timedelta(days=1)
    async with async_session() as s:
        session_tokens = (await s.execute(
            select(func.coalesce(func.sum(LLMCallLog.total_tokens), 0))
            .where(LLMCallLog.conversation_id == conversation_id)
        )).scalar() or 0
        user_tokens = (await s.execute(
            select(func.coalesce(func.sum(LLMCallLog.total_tokens), 0))
            .where(LLMCallLog.user_id == user_id, LLMCallLog.created_at >= since_day)
        )).scalar() or 0
        global_tokens = (await s.execute(
            select(func.coalesce(func.sum(LLMCallLog.total_tokens), 0))
            .where(LLMCallLog.created_at >= since_day)
        )).scalar() or 0

    if session_tokens >= settings.CHAT_SESSION_TOKEN_LIMIT:
        return BudgetCheck(False,
            f"This conversation has used its {settings.CHAT_SESSION_TOKEN_LIMIT:,}"
            "-token budget. Start a new conversation to keep going.")
    if user_tokens >= settings.CHAT_USER_DAILY_TOKEN_LIMIT:
        return BudgetCheck(False,
            f"You've reached today's {settings.CHAT_USER_DAILY_TOKEN_LIMIT:,}-token "
            "limit. Please try again tomorrow.")
    if global_tokens >= settings.CHAT_GLOBAL_DAILY_TOKEN_LIMIT:
        return BudgetCheck(False,
            "The advisor has hit today's platform-wide usage cap. Please try "
            "again later.")
    return BudgetCheck(True)
