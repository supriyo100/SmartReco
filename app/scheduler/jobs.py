"""APScheduler jobs. SINGLE WORKER ONLY (uvicorn --workers 1) — arch trap #3."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db.session import wal_checkpoint


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="Asia/Kolkata")
    # 30s outbox drain — TODO app.catalog.outbox.drain_outbox (batch embeds ▲A5)
    #   ▲B7: embed in chunks of 20; failed chunk → retry per-item; attempts+backoff
    #   absorb stragglers. Chroma upsert takes lists — one call per chunk.
    # 15m stale-rec refresh for active users — TODO
    sched.add_job(nightly_maintenance, "cron", hour=3, id="nightly")
    # 16:00 digest — BONUS. Ship POST /admin/trigger-digest first; cron only
    # if everything else is green by Aug 8 PM (arch v2 §8).
    return sched


async def nightly_maintenance():
    """▲A16: WAL checkpoint + orphan event cleanup + SQL↔Chroma reconcile."""
    await wal_checkpoint()
    # TODO: DELETE events WHERE user_id IS NULL AND ts < now-90d
    # TODO: reconcile products.content_hash vs Chroma metadata
