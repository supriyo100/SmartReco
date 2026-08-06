"""Admin: product CRUD, ingest trigger, agent-run observability (arch §1.1).

Every route here is behind `require_admin`, applied once as a router-level
dependency below.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.auth.deps import require_admin
from app.db.models import AgentRun, Event, Product, User, VectorOutbox
from app.db.session import async_session
from app.web.templating import render

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

LEVELS = ("beginner", "intermediate", "advanced")


def _split_list(raw: str) -> list[str]:
    """Comma-separated form field → list. Used for tags and ladder edges."""
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


async def _queue_outbox(session, product_id: int, op: str) -> None:
    """Any write to products enqueues a Chroma sync (§3.4 dual-write). Written
    in the same transaction as the product itself — that is the whole point of
    the outbox: SQLite and Chroma cannot diverge on a crash between them."""
    session.add(VectorOutbox(product_id=product_id, op=op))


@router.get("")
@router.get("/")
async def dashboard(request: Request):
    async with async_session() as s:
        counts = {
            "products": (await s.execute(
                select(func.count(Product.id)).where(Product.is_active.is_(True)))).scalar() or 0,
            "users": (await s.execute(select(func.count(User.id)))).scalar() or 0,
            "events": (await s.execute(select(func.count(Event.id)))).scalar() or 0,
            "outbox_pending": (await s.execute(
                select(func.count(VectorOutbox.id)).where(VectorOutbox.status == "pending"))).scalar() or 0,
        }
    return render(request, "admin/dashboard.html", counts=counts)


@router.get("/products")
async def list_products(request: Request, include_inactive: bool = False):
    async with async_session() as s:
        stmt = select(Product)
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        products = (await s.execute(stmt.order_by(Product.title))).scalars().all()
    return render(request, "admin/products.html", products=products,
                  include_inactive=include_inactive)


@router.get("/products/new")
async def new_product_form(request: Request):
    return render(request, "admin/product_form.html", p=None, levels=LEVELS)


@router.post("/products/new")
async def create_product(
    request: Request,
    title: str = Form(...),
    slug: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    level: str = Form("beginner"),
    price: float = Form(0.0),
    tags: str = Form(""),
    prereq_ids: str = Form(""),
    related_ids: str = Form(""),
    instructor: str = Form(""),
    rating: float = Form(4.5),
):
    slug = slug.strip().lower()
    async with async_session() as s:
        if (await s.execute(select(Product.id).where(Product.slug == slug))).scalar_one_or_none():
            return render(request, "admin/product_form.html", p=None, levels=LEVELS,
                          error=f"slug {slug!r} already exists")
        product = Product(
            title=title.strip(), slug=slug, description=description,
            category=category.strip(), level=level, price=price,
            tags=_split_list(tags), prereq_ids=_split_list(prereq_ids),
            related_ids=_split_list(related_ids), instructor=instructor.strip(),
            rating=rating,
        )
        s.add(product)
        await s.flush()               # need the PK for the outbox row
        await _queue_outbox(s, product.id, "upsert")
        await s.commit()
    return RedirectResponse("/admin/products", status_code=303)


@router.get("/products/{product_id}/edit")
async def edit_product_form(request: Request, product_id: int):
    async with async_session() as s:
        product = (await s.execute(
            select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if product is None:
        raise HTTPException(404, "product not found")
    return render(request, "admin/product_form.html", p=product, levels=LEVELS)


@router.post("/products/{product_id}/edit")
async def update_product(
    request: Request,
    product_id: int,
    title: str = Form(...),
    slug: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    level: str = Form("beginner"),
    price: float = Form(0.0),
    tags: str = Form(""),
    prereq_ids: str = Form(""),
    related_ids: str = Form(""),
    instructor: str = Form(""),
    rating: float = Form(4.5),
    is_active: bool = Form(False),
):
    slug = slug.strip().lower()
    async with async_session() as s:
        product = (await s.execute(
            select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if product is None:
            raise HTTPException(404, "product not found")
        clash = (await s.execute(
            select(Product.id).where(Product.slug == slug, Product.id != product_id)
        )).scalar_one_or_none()
        if clash:
            return render(request, "admin/product_form.html", p=product, levels=LEVELS,
                          error=f"slug {slug!r} belongs to another product")
        product.title = title.strip()
        product.slug = slug
        product.description = description
        product.category = category.strip()
        product.level = level
        product.price = price
        product.tags = _split_list(tags)
        product.prereq_ids = _split_list(prereq_ids)
        product.related_ids = _split_list(related_ids)
        product.instructor = instructor.strip()
        product.rating = rating
        product.is_active = is_active
        # Content changed, so the cached hash no longer describes it; clearing
        # forces the next ingest to re-embed rather than skip.
        product.content_hash = ""
        await _queue_outbox(s, product.id, "upsert" if is_active else "delete")
        await s.commit()
    return RedirectResponse("/admin/products", status_code=303)


@router.post("/products/{product_id}/delete")
async def delete_product(product_id: int):
    """Soft delete (arch §1.1): is_active=false, plus a Chroma delete so the
    course stops being retrievable. The row stays — events and stored
    recommendations reference it, and a hard delete would orphan them."""
    async with async_session() as s:
        product = (await s.execute(
            select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if product is None:
            raise HTTPException(404, "product not found")
        product.is_active = False
        await _queue_outbox(s, product.id, "delete")
        await s.commit()
    return RedirectResponse("/admin/products", status_code=303)


@router.post("/ingest")
async def trigger_ingest(request: Request):
    """Re-run the catalog ingest over data/data_1 (§2).

    ingest.main() is a CLI entry point: it reports by printing and returns None.
    Rather than reimplement it, capture its stdout and show that — the report it
    prints (per-course freshness, chunk counts) is exactly what an admin wants
    to see after an ingest.
    """
    import contextlib
    import io

    from app.catalog.ingest import main as ingest_main

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            await ingest_main(allow_pending=True)
    except SystemExit as exc:                 # loader.py fails loudly by design
        return render(request, "admin/dashboard.html", counts={},
                      error=str(exc), output=buf.getvalue())
    except Exception as exc:                  # surfaced in the UI, not a bare 500
        return render(request, "admin/dashboard.html", counts={},
                      error=f"{type(exc).__name__}: {exc}", output=buf.getvalue())
    return render(request, "admin/dashboard.html", counts={},
                  message="ingest complete", output=buf.getvalue())


@router.get("/sync")
async def sync_status(request: Request):
    """Dual-write health: what SQL holds vs what Chroma holds, and the queue
    between them. Divergence is meant to be visible, not inferred."""
    from app.catalog.outbox import status as outbox_status

    return render(request, "admin/sync.html", sync=await outbox_status())


@router.post("/sync")
async def run_sync(request: Request):
    """Drain the outbox now. The scheduler does this every 30s; this exists so
    an admin who just edited a product can confirm it reached Chroma rather
    than waiting and hoping."""
    from app.catalog.outbox import drain_all
    from app.catalog.outbox import status as outbox_status

    try:
        report = await drain_all()
    except Exception as exc:
        return render(request, "admin/sync.html", sync=await outbox_status(),
                      error=f"{type(exc).__name__}: {exc}")
    if "skipped" in report:
        return render(request, "admin/sync.html", sync=await outbox_status(),
                      error=report["skipped"])
    if "blocked" in report:
        return render(request, "admin/sync.html", sync=await outbox_status(),
                      error=report["blocked"])
    return render(request, "admin/sync.html", sync=await outbox_status(),
                  message=(f"synced — {report['upserted']} upserted, "
                           f"{report['deleted']} deleted, {report['failed']} failed"))


@router.get("/agent-runs")
async def agent_runs(request: Request, limit: int = 100):
    async with async_session() as s:
        runs = (await s.execute(
            select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
        )).scalars().all()
        avg_calls = (await s.execute(select(func.avg(AgentRun.llm_calls)))).scalar()
    return render(request, "admin/agent_runs.html", runs=runs, avg_calls=avg_calls)
