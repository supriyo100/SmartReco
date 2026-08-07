"""Chat endpoints: one to send a turn, two to read and reset the thread.

JSON rather than server-rendered, because the chat panel lives in `base.html`
on every page and must not navigate away from whatever the user is reading.
That is also why the panel is the one piece of client-side state in an
otherwise server-rendered app (§1.1): a chat that reloads the page on every
message is not a chat.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.auth.deps import require_user
from app.chat.agent import answer, get_or_create_conversation
from app.db.models import ChatMessage, Conversation, Product, User
from app.db.session import async_session

log = logging.getLogger("chat.routes")

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: int | None = None


def _card(p: Product) -> dict:
    return {"id": p.id, "slug": p.slug, "title": p.title,
            "category": p.category, "level": p.level,
            "price": float(p.price or 0.0), "rating": float(p.rating or 0.0)}


@router.post("")
async def send(payload: ChatIn, request: Request,
               user: User = Depends(require_user)):
    # The browser session id is the same one `events` carries, so a thread can
    # be correlated with what the person was browsing while they had it.
    session_id = getattr(request.state, "session_id", "") or ""
    conv = await get_or_create_conversation(user.id, payload.conversation_id,
                                            session_id=session_id)
    message = payload.message.strip()

    result = await answer(user.id, conv.id, message)
    intent = result.get("intent") or {}

    async with async_session() as s:
        s.add(ChatMessage(conversation_id=conv.id, role="user", content=message))
        s.add(ChatMessage(conversation_id=conv.id, role="assistant",
                          content=result["answer"],
                          cited_product_ids=result["cited"],
                          model_used=result["model"],
                          latency_ms=result["latency_ms"],
                          retrieval_path=result.get("retrieval_path", ""),
                          pathway=result.get("pathway") or {},
                          intent_level=intent.get("level", "cold"),
                          intent_score=float(intent.get("score") or 0.0)))
        # The first user message titles the thread — cheap, and better than
        # "Conversation 7" in a history list.
        row = (await s.execute(
            select(Conversation).where(Conversation.id == conv.id)
        )).scalar_one()
        if not row.title:
            row.title = message[:80]
        # Facts mined from the thread persist on the conversation, so the next
        # turn (and anything reading leads) sees them without re-deriving.
        if result.get("facts"):
            row.derived_facts = result["facts"]
        # Intent is a high-water mark: a user who asked "how do I enrol" in
        # turn 6 is still a warm lead in turn 9, even if turn 9 is a question
        # about scheduling. Downgrading on every neutral turn would make the
        # signal useless to anyone reading it later.
        if float(intent.get("score") or 0.0) > (row.intent_score or 0.0):
            row.intent_score = float(intent.get("score") or 0.0)
            row.intent_level = intent.get("level", "cold")
        await s.commit()

    # A budget or target role stated in conversation is as real as one typed
    # into the profile form, and the stored recommendation set does not know
    # about it yet. Only fires when the turn actually revealed something —
    # `maybe_generate` still applies the debounce on top.
    if result.get("facts", {}).get("target_role") or result.get("facts", {}).get("budget_max"):
        _kick_recommendations(user.id, "chat_facts")

    # Every retrieved course, for resolving the offer target. Cards are still
    # restricted to what the answer cited, below.
    by_id = {p.id: p for p in result["courses"]}
    # Only cards for courses the answer actually cited, in citation order, so
    # the panel can never render a course the text did not mention.
    cards = [_card(by_id[pid]) for pid in result["cited"] if pid in by_id]

    return {"answer": result["answer"], "conversation_id": conv.id,
            "cards": cards, "model": result["model"],
            "latency_ms": result["latency_ms"],
            "retrieval_path": result["retrieval_path"],
            "pathway": result.get("pathway"),
            # Only the level and the target reach the client. The score and
            # the matched patterns are internal — showing a user "we scored
            # your purchase intent at 0.81" is not a feature.
            "offer": _offer(intent, by_id)}


def _kick_recommendations(user_id: int, reason: str) -> None:
    """Background trigger — never on the response path of a chat turn."""
    import asyncio

    async def _run() -> None:
        from app.agent.graph import maybe_generate

        try:
            await maybe_generate(user_id, reason_hint=reason)
        except Exception:
            log.exception("chat recommendation trigger failed (user_id=%s)", user_id)

    asyncio.create_task(_run())


def _offer(intent: dict, by_id: dict[int, Product]) -> dict | None:
    """The CTA the panel should show, or None. Never shown at cold."""
    level = (intent or {}).get("level", "cold")
    product_id = (intent or {}).get("product_id")
    product = by_id.get(product_id)
    # No resolvable course means no offer. A CTA that links nowhere is worse
    # than no CTA, and the intent scorer only ever names retrieved courses.
    if level == "cold" or product is None:
        return None
    return {"level": level, "product_id": product.id, "slug": product.slug,
            "title": product.title,
            "text": (f"Ready to enrol in {product.title[:60]}?" if level == "hot"
                     else "Want to look at this one properly?")}


@router.get("/history")
async def history(conversation_id: int | None = None,
                  user: User = Depends(require_user)):
    """Replay the current thread so the panel survives a page navigation."""
    async with async_session() as s:
        conv = None
        if conversation_id:
            conv = (await s.execute(
                select(Conversation).where(Conversation.id == conversation_id,
                                           Conversation.user_id == user.id)
            )).scalar_one_or_none()
        if conv is None:
            conv = (await s.execute(
                select(Conversation).where(Conversation.user_id == user.id)
                .order_by(Conversation.updated_at.desc())
            )).scalars().first()
        if conv is None:
            return {"conversation_id": None, "messages": []}

        rows = (await s.execute(
            select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.created_at, ChatMessage.id).limit(100)
        )).scalars().all()

        cited_ids = {pid for m in rows for pid in (m.cited_product_ids or [])}
        cards: dict[int, dict] = {}
        if cited_ids:
            products = (await s.execute(
                select(Product).where(Product.id.in_(cited_ids),
                                      Product.is_active.is_(True))
            )).scalars().all()
            cards = {p.id: _card(p) for p in products}

    return {
        "conversation_id": conv.id,
        "messages": [
            {"role": m.role, "content": m.content,
             # A course deactivated since the answer was written is dropped
             # rather than rendered as a card that 404s.
             "cards": [cards[pid] for pid in (m.cited_product_ids or [])
                       if pid in cards],
             # The diagram as it was built at the time, not rebuilt against a
             # catalog that may have changed since.
             "pathway": m.pathway or None}
            for m in rows
        ],
    }


@router.post("/reset")
async def reset(user: User = Depends(require_user)):
    """Start a fresh thread. Deletes rather than archives — a chat history
    nobody can read is a privacy liability with no product value."""
    async with async_session() as s:
        ids = (await s.execute(
            select(Conversation.id).where(Conversation.user_id == user.id)
        )).scalars().all()
        if ids:
            await s.execute(delete(ChatMessage)
                            .where(ChatMessage.conversation_id.in_(ids)))
            await s.execute(delete(Conversation)
                            .where(Conversation.id.in_(ids)))
            await s.commit()
    return JSONResponse({"ok": True})
