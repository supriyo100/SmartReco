"""Drain vector_outbox → Chroma. The half of the dual-write that does the work.

The admin routes enqueue an outbox row in the same transaction as the product
write, so the intent to sync is as durable as the product itself. Until this
module ran, that was the entire mechanism: rows accumulated and nothing ever
consumed them, which meant SQLite and Chroma were guaranteed to diverge rather
than guaranteed to converge.

Why an outbox at all, rather than writing to Chroma inside the request: the two
stores cannot be committed atomically. Writing Chroma inline means a crash
between the two leaves them inconsistent with no record that a write was owed.
The outbox turns "write to two stores" into "write to one store, then replay" —
at-least-once delivery, made safe by upserts being idempotent.

Failure handling (▲B7): a batch that fails is retried per item, so one bad
product cannot block the queue behind it. Each attempt increments `attempts`;
past MAX_ATTEMPTS the row is parked as 'failed' with its error, visible on the
admin dashboard rather than retried forever.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.catalog import vectors
from app.config import settings
from app.db.models import Product, VectorOutbox
from app.db.session import async_session

log = logging.getLogger("outbox")

BATCH = 20          # embeds per API call (▲B7)
MAX_ATTEMPTS = 5

# Statuses where retrying cannot help: the request was understood and refused.
# 401/403 = bad key, 402 = no balance, 404 = no such model. Retrying these
# burns real API calls to receive the same refusal — and with per-item retry on
# top of a batch, one billing error becomes N+1 pointless calls per drain.
FATAL_STATUS = {400, 401, 402, 403, 404}


def _is_fatal(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status in FATAL_STATUS


def _explain(exc: Exception) -> str:
    if _is_fatal(exc):
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status == 402:
            return ("Mesh account has no balance (402 spend_limit_exceeded) — "
                    "top up, then press Sync now. Nothing was lost; the queue is intact.")
        return f"Mesh rejected the request ({status}) — check MESH_API_KEY and model names."
    return ""


async def _mark(session, row: VectorOutbox, status: str, error: str = "") -> None:
    row.status = status
    row.attempts += 1
    row.last_error = error[:500]


async def drain_once(limit: int = BATCH) -> dict:
    """Process one batch of pending rows. Returns a report.

    Safe to call when Mesh is unconfigured: it reports skipped rather than
    burning attempts on work that cannot succeed without a key.
    """
    if not settings.can_embed:
        return {"skipped": "embeddings disabled (ENV=test) — Chroma sync deferred",
                "upserted": 0, "deleted": 0, "failed": 0}

    async with async_session() as s:
        rows = (await s.execute(
            select(VectorOutbox)
            .where(VectorOutbox.status == "pending",
                   VectorOutbox.attempts < MAX_ATTEMPTS)
            .order_by(VectorOutbox.created_at)
            .limit(limit)
        )).scalars().all()

        if not rows:
            return {"upserted": 0, "deleted": 0, "failed": 0, "pending_left": 0}

        # Collapse to the LAST op per product. Five edits between drains are one
        # upsert of the current state, not five embeds of superseded versions.
        latest: dict[int, VectorOutbox] = {}
        for row in rows:
            latest[row.product_id] = row

        upsert_ids = [pid for pid, r in latest.items() if r.op == "upsert"]
        delete_ids = [pid for pid, r in latest.items() if r.op == "delete"]

        upserted = deleted = failed = 0

        for pid in delete_ids:
            try:
                vectors.delete_product(pid)
                deleted += 1
                await _mark(s, latest[pid], "done")
            except Exception as exc:
                failed += 1
                log.exception("outbox delete failed for product %s", pid)
                await _mark(s, latest[pid], "pending", f"{type(exc).__name__}: {exc}")

        if upsert_ids:
            products = (await s.execute(
                select(Product).where(Product.id.in_(upsert_ids)))).scalars().all()
            found = {p.id for p in products}

            # A queued product that no longer exists is a completed job, not a
            # failure — retrying it five times would only delay the queue.
            for pid in set(upsert_ids) - found:
                await _mark(s, latest[pid], "done", "product row no longer exists")

            # Skip products whose embedded content is unchanged since the last
            # sync. An admin editing `price` (not embedded) shouldn't cost an
            # embedding call.
            stale = [p for p in products if p.content_hash != vectors.content_hash(p)]
            stale_ids = {p.id for p in stale}
            for p in products:
                if p.id not in stale_ids:
                    await _mark(s, latest[p.id], "done", "content unchanged")

            if stale:
                try:
                    await vectors.upsert_products(stale)
                    for p in stale:
                        p.content_hash = vectors.content_hash(p)
                        await _mark(s, latest[p.id], "done")
                    upserted = len(stale)
                except Exception as batch_exc:
                    if _is_fatal(batch_exc):
                        # Account/key/model problem: every item would fail the
                        # same way. Retrying per item would turn one refusal
                        # into len(stale) more. Leave the rows pending WITHOUT
                        # charging an attempt — the work is still owed, and it
                        # will succeed unchanged once the account is fixed.
                        note = _explain(batch_exc)
                        log.error("outbox drain halted: %s", note)
                        for p in stale:
                            latest[p.id].last_error = note[:500]
                        await s.commit()
                        return {"upserted": 0, "deleted": deleted, "failed": 0,
                                "pending_left": len(stale), "blocked": note}
                    # Transient or item-specific: retry each so one poison row
                    # cannot block every product queued behind it (▲B7).
                    log.warning("batch upsert failed; retrying %d individually", len(stale))
                    for p in stale:
                        try:
                            await vectors.upsert_products([p])
                            p.content_hash = vectors.content_hash(p)
                            await _mark(s, latest[p.id], "done")
                            upserted += 1
                        except Exception as exc:
                            failed += 1
                            log.warning("outbox upsert failed for product %s: %s", p.id, exc)
                            row = latest[p.id]
                            status = "failed" if row.attempts + 1 >= MAX_ATTEMPTS else "pending"
                            await _mark(s, row, status, f"{type(exc).__name__}: {exc}")

        await s.commit()

        pending_left = (await s.execute(
            select(func.count(VectorOutbox.id)).where(VectorOutbox.status == "pending")
        )).scalar() or 0

    return {"upserted": upserted, "deleted": deleted, "failed": failed,
            "pending_left": pending_left}


async def drain_all(max_batches: int = 50) -> dict:
    """Drain until empty or max_batches. The bound matters: without it, a row
    that fails and stays pending would spin here forever."""
    total = {"upserted": 0, "deleted": 0, "failed": 0, "pending_left": 0, "batches": 0}
    for _ in range(max_batches):
        report = await drain_once()
        if "skipped" in report:
            report["batches"] = total["batches"]
            return report
        total["batches"] += 1
        for k in ("upserted", "deleted", "failed"):
            total[k] += report[k]
        total["pending_left"] = report["pending_left"]
        if "blocked" in report:
            # Nothing will succeed until the account/key is fixed; 49 more
            # batches would produce 49 more identical refusals.
            total["blocked"] = report["blocked"]
            break
        if report["pending_left"] == 0 or (
                report["upserted"] == 0 and report["deleted"] == 0):
            break
    return total


async def status() -> dict:
    """Queue health for the admin dashboard — divergence should be visible."""
    async with async_session() as s:
        by_status = dict((await s.execute(
            select(VectorOutbox.status, func.count(VectorOutbox.id))
            .group_by(VectorOutbox.status))).all())
        products = (await s.execute(
            select(func.count(Product.id)).where(Product.is_active.is_(True)))).scalar() or 0
    out = {"pending": by_status.get("pending", 0),
           "done": by_status.get("done", 0),
           "failed": by_status.get("failed", 0),
           "active_products": products,
           "chroma_chunks": None}
    try:
        out["chroma_chunks"] = vectors.count()
    except Exception as exc:            # Chroma absent is a status, not a 500
        out["chroma_error"] = f"{type(exc).__name__}: {exc}"
    return out


if __name__ == "__main__":
    import asyncio
    import sys

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8", errors="replace")
    print(asyncio.run(drain_all()))
