"""Chat-agent-specific middleware: the grounding guarantee and the offline
degradation path, both ported from the pre-agent single-shot implementation
in app/chat/agent.py so a tool-calling agent keeps the same guarantees a
single retrieve-then-generate call used to provide for free.
"""
from __future__ import annotations

import logging
import re

from langchain.agents.middleware import dynamic_prompt, wrap_model_call
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

log = logging.getLogger("chat.agent_middleware")

_ID_RE = re.compile(r"id:(\d+)")


@dynamic_prompt
def chat_system_prompt(request: ModelRequest) -> str:
    """The per-turn system prompt: the fixed advisor rules plus the
    profile/resume/history knowledge block built for this specific turn.

    `create_agent`'s `system_prompt=` is a single fixed string set once at
    import time — too early for anything user-specific. `ChatContext.knowledge`
    (set per invocation in `answer()`) is what makes this dynamic instead.
    """
    from app.chat.agent import SYSTEM_PROMPT

    ctx = request.runtime.context if request.runtime else None
    knowledge = getattr(ctx, "knowledge", "") if ctx else ""
    return f"{SYSTEM_PROMPT}\n\n{knowledge}" if knowledge else SYSTEM_PROMPT


def _cited_ids_from_tool_results(messages: list) -> set[int]:
    """Every course id any tool call surfaced since the last human turn.

    Scoped to "this turn" rather than the whole thread: a course that was
    retrieved and discussed several turns ago is not implicitly re-grounded
    just because it is still in history — the model has to have actually
    looked it up (again) to cite it now.
    """
    ids: set[int] = set()
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
            ids.update(int(m) for m in _ID_RE.findall(msg.content))
    return ids


def _parse_course_lines(text: str) -> list[dict]:
    """Parse `_fmt_course()`-shaped tool output back into plain dicts.

    `_fmt_course` (app/chat/agent.py) is the one place course-formatting
    logic lives; this is its inverse, used only by the offline fallback below
    to build a bullet list without a second round trip to the database.
    """
    out = []
    for line in (text or "").splitlines():
        bits = dict(b.split(":", 1) for b in line.split(" | ") if ":" in b)
        if "id" not in bits:
            continue
        title = line.split(" | ")[0].split("id:" + bits["id"] + " ", 1)[-1].strip()
        out.append({"id": int(bits["id"]), "title": title or f"course {bits['id']}",
                    "category": bits.get("category", ""), "level": bits.get("level", ""),
                    "price": bits.get("price", "")})
    return out


@wrap_model_call(name="GroundingMiddleware")
async def enforce_grounding(request: ModelRequest, handler):
    """After the model's final reply (no further tool calls), strip any
    citation — and any invented course title riding with it — for an id the
    agent did not actually retrieve this turn.

    Reuses `_enforce_grounding()` from app/chat/agent.py unchanged; the only
    thing that changed with tool-calling is where the "allowed ids" set comes
    from — every `search_catalog`/`get_course_details` call this turn, not
    one retrieval before a single prompt.
    """
    from app.chat.agent import _enforce_grounding

    response = await handler(request)
    if not response.result:
        return response

    last = response.result[-1]
    if not isinstance(last, AIMessage) or last.tool_calls or not last.content:
        return response

    allowed = _cited_ids_from_tool_results(request.messages)
    text, _cited = _enforce_grounding(str(last.content), allowed)
    if text == last.content:
        return response

    cleaned = last.model_copy(update={"content": text})
    return ModelResponse(result=[*response.result[:-1], cleaned],
                         structured_response=response.structured_response)


@wrap_model_call(name="OfflineReplyMiddleware")
async def offline_reply(request: ModelRequest, handler):
    """Outermost fallback: if every model provider is exhausted (see
    `app/agent/langchain_bridge.py:mesh_fallback_middleware`), answer from
    whatever `search_catalog` already found this turn instead of failing the
    request. Visibly a fallback, same as the pre-agent `_offline_reply()` —
    it never pretends to be the model.
    """
    try:
        return await handler(request)
    except Exception as exc:
        log.warning("chat agent: all model providers failed (%s); offline reply", exc)

    courses = []
    seen = set()
    for msg in reversed(request.messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
            for row in _parse_course_lines(msg.content):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    courses.append(row)

    if not courses:
        text = ("I can't reach the recommendation model right now, and I "
                "don't have any matching courses from this conversation yet. "
                "Try naming a specific skill — 'RAG', 'LangGraph', 'MLOps' — "
                "and I'll take another look.")
    else:
        lines = ["I'm running without the language model right now, so here "
                "is a direct catalog match rather than tailored advice:"]
        for c in courses[:3]:
            price = c["price"] or "—"
            lines.append(f"- **{c['title']}** [[id:{c['id']}]] — "
                         f"{c['category']}, {c['level']}, {price}")
        text = "\n".join(lines)

    return AIMessage(content=text)
