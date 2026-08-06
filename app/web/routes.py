"""The recommendations surface (arch §1.1, §6).

Reads the stored current recommendation set. It deliberately does not invoke the
agent — generation is trigger-driven (§5.1), and putting it behind a page load
would put an LLM call on the critical path of every visit, which is the thing
the whole planner exists to avoid.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.auth.deps import require_user
from app.db.models import Product, Recommendation, User
from app.db.session import async_session
from app.web.templating import render

router = APIRouter(tags=["web"])


@router.get("/recommendations")
async def recommendations(request: Request, user: User = Depends(require_user)):
    async with async_session() as s:
        rec = (await s.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user.id, Recommendation.is_current.is_(True))
            .order_by(Recommendation.created_at.desc())
        )).scalars().first()

        cards = []
        if rec and rec.items:
            ids = [i.get("product_id") for i in rec.items if i.get("product_id")]
            by_id = {}
            if ids:
                by_id = {p.id: p for p in (await s.execute(
                    select(Product).where(Product.id.in_(ids))
                )).scalars().all()}
            # A recommendation whose product was deactivated since generation is
            # dropped rather than rendered as a broken card.
            for item in sorted(rec.items, key=lambda i: i.get("rank", 0)):
                product = by_id.get(item.get("product_id"))
                if product is not None and product.is_active:
                    cards.append({"item": item, "p": product})

    return render(request, "web/recommendations.html", rec=rec, cards=cards)
