"""Per-call LLM telemetry: one row in `llm_call_log` per provider attempt.

Written from both call paths that actually reach a model — the OpenAI-SDK
one (`app/agent/mesh.py:_chat`, used by the recommendation pipeline) and the
LangChain one (`app/agent/langchain_bridge.py:mesh_fallback_middleware`,
used by the chat agent) — so a token/latency dashboard sees every call
regardless of which stack made it.

Same rule as `app/agent/graph.py:_record_run`: never raises. A telemetry
write must not be able to fail the LLM call it is recording.
"""
from __future__ import annotations

import logging

from app.db.models import LLMCallLog
from app.db.session import async_session

log = logging.getLogger("agent.telemetry")


async def record_llm_call(
    *, kind: str, provider: str, model: str = "", user_id: int | None = None,
    conversation_id: int | None = None, attempt: int = 1, is_fallback: bool = False,
    prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0,
    latency_ms: int = 0, status: str = "ok", error: str = "",
) -> None:
    try:
        async with async_session() as s:
            s.add(LLMCallLog(
                kind=kind, provider=provider, model=model, user_id=user_id,
                conversation_id=conversation_id, attempt=attempt, is_fallback=is_fallback,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                total_tokens=total_tokens, latency_ms=latency_ms,
                status=status, error=error[:500],
            ))
            await s.commit()
    except Exception:
        log.warning("could not record llm_call_log (%s/%s)", provider, kind)
