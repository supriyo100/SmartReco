"""APScheduler jobs. SINGLE WORKER ONLY (uvicorn --workers 1) — arch trap #3."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db.session import wal_checkpoint

log = logging.getLogger("scheduler")


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="Asia/Kolkata")
    # 30s outbox drain: SQL→Chroma convergence (▲A5 batch embeds, ▲B7 per-item
    # retry). max_instances=1 + coalesce so a drain slower than 30s cannot
    # overlap itself and embed the same product twice concurrently.
    sched.add_job(drain_outbox, "interval", seconds=30, id="outbox_drain",
                  max_instances=1, coalesce=True)
    # 15m stale-rec refresh for active users — TODO
    sched.add_job(nightly_maintenance, "cron", hour=3, id="nightly")
    # 16:00 digest — BONUS. Ship POST /admin/trigger-digest first; cron only
    # if everything else is green by Aug 8 PM (arch v2 §8).
    return sched


async def drain_outbox():
    """Replay pending product writes into Chroma (§3.4 dual-write).

    Exceptions are swallowed deliberately: an APScheduler job that raises is
    logged and the SCHEDULE CONTINUES, but a job raising every 30s buries the
    log. drain_once already handles per-row failure and records the error on
    the row itself, so anything reaching here is unexpected and worth one line.
    """
    from app.catalog.outbox import drain_once

    try:
        report = await drain_once()
    except Exception:
        log.exception("outbox drain failed")
        return
    if report.get("upserted") or report.get("deleted") or report.get("failed"):
        log.info("outbox drain: %s", report)


async def nightly_maintenance():
    """▲A16: WAL checkpoint + orphan event cleanup + SQL↔Chroma reconcile."""
    await wal_checkpoint()
    # TODO: DELETE events WHERE user_id IS NULL AND ts < now-90d
    # TODO: reconcile products.content_hash vs Chroma metadata
