"""Public catalog browsing: landing, search, course detail (arch §1.1 route map).

These are the tracked surfaces — every page here is instrumented by tracker.js,
and course detail is the main behavioral signal.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import distinct, select, text

from app.auth.deps import current_user, require_user
from app.catalog.enrollment import course_purchase_snapshot, enrollment_status, parse_access_window
from app.db.models import Enrollment, Product, User
from app.db.session import async_session
from app.web.templating import render

router = APIRouter(tags=["catalog"])

PAGE_SIZE = 24


@router.get("/")
async def landing(request: Request, category: str | None = None, level: str | None = None):
    async with async_session() as s:
        stmt = select(Product).where(Product.is_active.is_(True))
        if category:
            stmt = stmt.where(Product.category == category)
        if level:
            stmt = stmt.where(Product.level == level)
        products = (await s.execute(stmt.order_by(Product.rating.desc()).limit(PAGE_SIZE))).scalars().all()
        categories = (await s.execute(
            select(distinct(Product.category)).where(Product.is_active.is_(True)).order_by(Product.category)
        )).scalars().all()
    return render(request, "catalog/index.html", products=products,
                  categories=categories, category=category, level=level)


@router.get("/search")
async def search(request: Request, q: str = Query("", max_length=200)):
    """FTS5 over products_fts (built in init_db), joined back for the full row.

    The query goes through a bind parameter and FTS5 MATCH, so it is not SQL
    injection — but it *is* FTS5 query syntax, and a stray quote or bare NEAR
    raises. A malformed search should return nothing, not a 500.
    """
    q = q.strip()
    products, error = [], None
    if q:
        async with async_session() as s:
            try:
                rows = (await s.execute(
                    text("SELECT rowid FROM products_fts WHERE products_fts MATCH :q "
                         "ORDER BY rank LIMIT :n"),
                    {"q": q, "n": PAGE_SIZE},
                )).scalars().all()
            except Exception:
                rows, error = [], "Could not parse that search. Try plain words."
            if rows:
                found = (await s.execute(
                    select(Product).where(Product.id.in_(rows), Product.is_active.is_(True))
                )).scalars().all()
                order = {pid: i for i, pid in enumerate(rows)}
                products = sorted(found, key=lambda p: order.get(p.id, 1 << 30))
    return render(request, "catalog/search.html", q=q, products=products, error=error)


@router.get("/course/{slug}")
async def course_detail(request: Request, slug: str):
    async with async_session() as s:
        product = (await s.execute(
            select(Product).where(Product.slug == slug)
        )).scalar_one_or_none()
        if product is None or not product.is_active:
            raise HTTPException(404, "course not found")
        # Ladder edges are slugs (§3.4); resolve them for the "next step" links.
        ref_slugs = list(product.prereq_ids or []) + list(product.related_ids or [])
        related = []
        if ref_slugs:
            related = (await s.execute(
                select(Product).where(Product.slug.in_(ref_slugs), Product.is_active.is_(True))
            )).scalars().all()
    prereqs = [p for p in related if p.slug in (product.prereq_ids or [])]
    nexts = [p for p in related if p.slug in (product.related_ids or [])]
    return render(request, "catalog/detail.html", p=product, prereqs=prereqs, nexts=nexts)


@router.get("/course/{slug}/enroll")
async def enroll_landing(request: Request, slug: str,
                         user: User | None = Depends(current_user)):
    """The buy/landing page an "Enroll" click opens: course terms, cohort or
    access info pulled from the curated JSON (Product doesn't carry it), and
    a mocked checkout — no payment gateway is wired up yet, so submitting it
    records the enrollment directly."""
    async with async_session() as s:
        product = (await s.execute(
            select(Product).where(Product.slug == slug)
        )).scalar_one_or_none()
        if product is None or not product.is_active:
            raise HTTPException(404, "course not found")
        existing = None
        if user is not None:
            existing = (await s.execute(
                select(Enrollment)
                .where(Enrollment.user_id == user.id, Enrollment.product_id == product.id)
                .order_by(Enrollment.created_at.desc())
            )).scalars().first()
    return render(request, "catalog/enroll.html", p=product,
                  snap=course_purchase_snapshot(slug), user=user,
                  existing=existing,
                  existing_status=enrollment_status(existing) if existing else None,
                  success=request.query_params.get("success") == "1")


@router.post("/course/{slug}/enroll")
async def enroll_submit(slug: str, user: User = Depends(require_user)):
    async with async_session() as s:
        product = (await s.execute(
            select(Product).where(Product.slug == slug)
        )).scalar_one_or_none()
        if product is None or not product.is_active:
            raise HTTPException(404, "course not found")

        current = (await s.execute(
            select(Enrollment)
            .where(Enrollment.user_id == user.id, Enrollment.product_id == product.id)
            .order_by(Enrollment.created_at.desc())
        )).scalars().first()
        # Already have live access: don't create a duplicate row on a
        # resubmit (double-click, back-button-and-repost).
        if current is not None and enrollment_status(current) == "active":
            await s.commit()
            return RedirectResponse(f"/course/{slug}/enroll?success=1", status_code=303)

        snap = course_purchase_snapshot(slug)
        window = parse_access_window(snap["access_text"])
        now = datetime.utcnow()
        s.add(Enrollment(
            user_id=user.id, product_id=product.id, mode=snap["mode"],
            cohort_start=snap["cohort_start"],
            access_expires_at=(now + window) if window else None,
            price_paid=product.price or 0.0,
        ))
        await s.commit()
    return RedirectResponse(f"/course/{slug}/enroll?success=1", status_code=303)
