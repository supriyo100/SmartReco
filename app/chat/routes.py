"""Chat endpoints: one to send a turn, two to read and reset the thread.

JSON rather than server-rendered, because the chat panel lives in `base.html`
on every page and must not navigate away from whatever the user is reading.
That is also why the panel is the one piece of client-side state in an
otherwise server-rendered app (§1.1): a chat that reloads the page on every
message is not a chat.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
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
async def send(payload: ChatIn, user: User = Depends(require_user)):
    conv = await get_or_create_conversation(user.id, payload.conversation_id)
    message = payload.message.strip()

    result = await answer(user.id, conv.id, message)

    async with async_session() as s:
        s.add(ChatMessage(conversation_id=conv.id, role="user", content=message))
        s.add(ChatMessage(conversation_id=conv.id, role="assistant",
                          content=result["answer"],
                          cited_product_ids=result["cited"],
                          model_used=result["model"],
                          latency_ms=result["latency_ms"]))
        # The first user message titles the thread — cheap, and better than
        # "Conversation 7" in a history list.
        row = (await s.execute(
            select(Conversation).where(Conversation.id == conv.id)
        )).scalar_one()
        if not row.title:
            row.title = message[:80]
        await s.commit()

    # Only cards for courses the answer actually cited, in citation order, so
    # the panel can never render a course the text did not mention.
    by_id = {p.id: p for p in result["courses"]}
    cards = [_card(by_id[pid]) for pid in result["cited"] if pid in by_id]

    return {"answer": result["answer"], "conversation_id": conv.id,
            "cards": cards, "model": result["model"],
            "latency_ms": result["latency_ms"],
            "retrieval_path": result["retrieval_path"]}


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
                       if pid in cards]}
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
