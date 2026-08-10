"""Admin: product CRUD, ingest trigger, agent-run observability (arch §1.1).

Every route here is behind `require_admin`, applied once as a router-level
dependency below.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import case, func, select

from app.auth.deps import require_admin
from app.config import settings
from app.db.models import (
    AgentRun,
    ChatMessage,
    DigestLog,
    Event,
    LLMCallLog,
    Product,
    User,
    VectorOutbox,
)
from app.db.session import async_session
from app.mail.sender import MAIL_DIR
from app.web.templating import render

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

LEVELS = ("beginner", "intermediate", "advanced")

# (key, label, what it needs to produce anything) — drives the admin mail page,
# so adding a template means adding one row here rather than editing markup.
MAIL_KINDS = (
    ("digest", "Daily digest", "a current recommendation set"),
    ("welcome", "Welcome / onboarding", "nothing — always renders"),
    ("ats", "Resume / ATS report", "a current resume analysis"),
    ("reengage", "Re-engagement nudge", "a past product view"),
)


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


@router.get("/mail")
async def mail_dashboard(request: Request, message: str = "", error: str = ""):
    """What the mail system would do, and to whom.

    Shows configuration state first because the single most common confusion is
    "I pressed send and nothing arrived" — when in fact SMTP was unconfigured
    and the message is sitting in data/outbox_mail/ exactly as designed.
    """
    from app.mail.digest import _today

    async with async_session() as s:
        users = (await s.execute(
            select(User).where(User.is_active.is_(True)).order_by(User.email)
        )).scalars().all()
        sent_today = {row for row in (await s.execute(
            select(DigestLog.user_id).where(DigestLog.sent_date == _today())
        )).scalars().all()}

    return render(request, "admin/mail.html",
                  users=users, sent_today=sent_today, today=_today(),
                  smtp_configured=settings.smtp_configured,
                  smtp_host=settings.SMTP_HOST,
                  smtp_user=settings.SMTP_USER,
                  mail_from=settings.mail_from,
                  digest_hour=settings.DIGEST_HOUR,
                  base_url=settings.PUBLIC_BASE_URL,
                  outbox_dir=str(MAIL_DIR),
                  kinds=MAIL_KINDS,
                  message=message, error=error)


@router.post("/mail/send")
async def send_one_mail(request: Request, kind: str = Form(...),
                        user_id: int = Form(...)):
    """Send one message of one kind to one user, now.

    `force=True` throughout: an admin who picked a user and a template is
    explicitly asking for that send, so the opt-out and the once-a-day digest
    log are both bypassed. That is the point of the button — it exists to test
    and demo the templates, and a preview that silently declines to send is
    useless for both.
    """
    from app.mail.messages import send_ats_report, send_digest, send_reengage, send_welcome

    senders = {
        "digest": lambda uid: send_digest(uid, force=True),
        "welcome": send_welcome,
        "ats": lambda uid: send_ats_report(uid, force=True),
        "reengage": lambda uid: send_reengage(uid, force=True),
    }
    if kind not in senders:
        return await mail_dashboard(request, error=f"unknown mail kind {kind!r}")

    try:
        result = await senders[kind](user_id)
    except Exception as exc:
        return await mail_dashboard(
            request, error=f"{kind}: {type(exc).__name__}: {exc}")

    if not result.ok:
        return await mail_dashboard(request, error=f"{kind}: {result.detail}")
    if result.mode == "skipped":
        return await mail_dashboard(
            request, message=f"{kind}: nothing to send — {result.detail}")
    if result.mode == "stored":
        return await mail_dashboard(
            request,
            message=(f"{kind}: SMTP not configured, so the rendered message was "
                     f"written to {result.detail}"))
    return await mail_dashboard(request, message=f"{kind}: sent")


@router.post("/mail/digest-run")
async def trigger_digest(request: Request, force: str = Form("")):
    """Run the whole digest fan-out now, as the 16:00 cron would.

    Unforced, this is genuinely idempotent — press it twice and the second run
    reports everyone as skipped, because DigestLog already holds today's rows.
    """
    from app.mail.digest import run_digest

    try:
        report = await run_digest(force=bool(force))
    except Exception as exc:
        return await mail_dashboard(request,
                                    error=f"digest run: {type(exc).__name__}: {exc}")
    summary = (f"digest run — {report['sent']} sent, {report['stored']} stored, "
               f"{report['skipped']} skipped, {report['failed']} failed "
               f"(of {report['considered']} considered)")
    if report["errors"]:
        return await mail_dashboard(request, message=summary,
                                    error="; ".join(report["errors"][:5]))
    return await mail_dashboard(request, message=summary)


@router.get("/mail/preview/{kind}/{user_id}")
async def preview_mail(request: Request, kind: str, user_id: int):
    """Render a message to the browser without sending it.

    Worth having separately from the send button: iterating on template markup
    by mailing yourself and waiting for delivery is slow, and the HTML an email
    client shows is the HTML this returns.
    """
    from fastapi.responses import HTMLResponse

    from app.mail import preview

    try:
        html = await preview.render_preview(kind, user_id)
    except Exception as exc:
        return await mail_dashboard(request,
                                    error=f"preview {kind}: {type(exc).__name__}: {exc}")
    if html is None:
        return await mail_dashboard(
            request, message=f"{kind}: nothing to render for user {user_id} "
                             f"(no recommendation, resume, or activity yet)")
    return HTMLResponse(html)


@router.get("/agent-runs")
async def agent_runs(request: Request, limit: int = 100):
    async with async_session() as s:
        runs = (await s.execute(
            select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
        )).scalars().all()
        avg_calls = (await s.execute(select(func.avg(AgentRun.llm_calls)))).scalar()
    return render(request, "admin/agent_runs.html", runs=runs, avg_calls=avg_calls)


@router.get("/settings")
async def settings_page(request: Request, message: str = "", error: str = ""):
    """Mesh API key + model picker (arch: admin-editable, encrypted key,
    write-through to .env — see app/admin/secrets_store.py).

    The model dropdowns are populated from a live GET to Mesh's /models, so
    an admin can only pick a model Mesh actually serves. That call can fail
    (bad/exhausted key, network) — models=[] then, and the template falls
    back to a plain text input rather than an empty, unusable <select>.
    """
    from app.admin.secrets_store import mask
    from app.agent.mesh import list_models

    models = await list_models()
    return render(request, "admin/settings.html",
                  mesh_key_masked=mask(settings.MESH_API_KEY),
                  mesh_configured=bool(settings.MESH_API_KEY),
                  models=models,
                  model_fast=settings.MODEL_FAST,
                  model_fast_fallback=settings.MODEL_FAST_FALLBACK,
                  model_writer=settings.MODEL_WRITER,
                  embed_model=settings.EMBED_MODEL,
                  smtp_configured=settings.smtp_configured,
                  smtp_host=settings.SMTP_HOST,
                  smtp_port=settings.SMTP_PORT,
                  smtp_user=settings.SMTP_USER,
                  smtp_pass_masked=mask(settings.SMTP_PASS),
                  mail_from=settings.MAIL_FROM,
                  mail_from_name=settings.MAIL_FROM_NAME,
                  message=message, error=error)


@router.post("/settings/mesh-key")
async def update_mesh_key(request: Request, mesh_api_key: str = Form(...)):
    """Blank submits are rejected rather than silently clearing the key —
    the field is a password input, so a browser autofill quirk or a stray
    submit with nothing typed must not wipe a working key."""
    from app.admin.secrets_store import set_mesh_api_key

    mesh_api_key = mesh_api_key.strip()
    if not mesh_api_key:
        return await settings_page(request, error="Mesh API key cannot be blank")
    await set_mesh_api_key(mesh_api_key)
    return RedirectResponse("/admin/settings?message=Mesh+API+key+updated", status_code=303)


@router.post("/settings/models")
async def update_models(
    request: Request,
    model_fast: str = Form(...),
    model_fast_fallback: str = Form(...),
    model_writer: str = Form(...),
    embed_model: str = Form(...),
):
    from app.admin.secrets_store import set_models

    await set_models(model_fast=model_fast, model_fast_fallback=model_fast_fallback,
                     model_writer=model_writer, embed_model=embed_model)
    return RedirectResponse("/admin/settings?message=Model+selection+updated", status_code=303)


@router.post("/settings/smtp")
async def update_smtp(
    request: Request,
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_pass: str = Form(""),
    mail_from: str = Form(""),
    mail_from_name: str = Form("SmartReco"),
):
    """Blank host/user clears SMTP back to the credential-less outbox mode
    (app/mail/sender.py) — that's a supported state, not an error, so unlike
    the Mesh key form this one accepts empty fields. smtp_pass alone is
    special-cased: blank there means 'leave the stored password as-is', since
    a password field is never pre-filled with the real secret."""
    from app.admin.secrets_store import set_smtp_settings

    await set_smtp_settings(smtp_host=smtp_host, smtp_port=smtp_port, smtp_user=smtp_user,
                            smtp_pass=smtp_pass, mail_from=mail_from,
                            mail_from_name=mail_from_name)
    return RedirectResponse("/admin/settings?message=SMTP+settings+updated", status_code=303)


@router.get("/llm-usage")
async def llm_usage(request: Request, limit: int = 100):
    """Token/latency/cost telemetry from `llm_call_log` (app/agent/telemetry.py)
    — every provider attempt from both the chat agent and the recommendation
    pipeline, plus how many turns the usage guardrails (app/chat/guardrails.py)
    have actually blocked. Rolling 24h window: a same-day view of "what is
    this costing right now", not a historical archive.
    """
    since_day = datetime.utcnow() - timedelta(days=1)
    async with async_session() as s:
        calls_24h, tokens_24h, avg_latency_24h, errors_24h = (await s.execute(
            select(func.count(LLMCallLog.id),
                  func.coalesce(func.sum(LLMCallLog.total_tokens), 0),
                  func.coalesce(func.avg(LLMCallLog.latency_ms), 0),
                  func.coalesce(func.sum(case((LLMCallLog.status == "error", 1), else_=0)), 0))
            .where(LLMCallLog.created_at >= since_day)
        )).one()

        by_provider = (await s.execute(
            select(LLMCallLog.provider, func.count(LLMCallLog.id),
                  func.coalesce(func.sum(LLMCallLog.total_tokens), 0),
                  func.coalesce(func.avg(LLMCallLog.latency_ms), 0))
            .where(LLMCallLog.created_at >= since_day)
            .group_by(LLMCallLog.provider)
            .order_by(func.sum(LLMCallLog.total_tokens).desc())
        )).all()

        by_kind = (await s.execute(
            select(LLMCallLog.kind, func.count(LLMCallLog.id),
                  func.coalesce(func.sum(LLMCallLog.total_tokens), 0))
            .where(LLMCallLog.created_at >= since_day)
            .group_by(LLMCallLog.kind)
            .order_by(func.sum(LLMCallLog.total_tokens).desc())
        )).all()

        recent = (await s.execute(
            select(LLMCallLog).order_by(LLMCallLog.created_at.desc()).limit(limit)
        )).scalars().all()

        blocked_24h = (await s.execute(
            select(func.count(ChatMessage.id))
            .where(ChatMessage.model_used == "budget-exceeded",
                  ChatMessage.created_at >= since_day)
        )).scalar()

    max_provider_tokens = max((row[2] for row in by_provider), default=0) or 1

    return render(request, "admin/llm_usage.html",
                 calls_24h=calls_24h, tokens_24h=tokens_24h,
                 avg_latency_24h=avg_latency_24h, errors_24h=errors_24h,
                 blocked_24h=blocked_24h or 0, by_provider=by_provider,
                 by_kind=by_kind, max_provider_tokens=max_provider_tokens,
                 recent=recent,
                 session_limit=settings.CHAT_SESSION_TOKEN_LIMIT,
                 user_daily_limit=settings.CHAT_USER_DAILY_TOKEN_LIMIT,
                 global_daily_limit=settings.CHAT_GLOBAL_DAILY_TOKEN_LIMIT)
