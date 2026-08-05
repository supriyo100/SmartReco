"""asyncio queue + batch writer. Handler does validation + put — nothing else."""
import asyncio
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import Event
from app.db.session import async_session

log = logging.getLogger("tracking")

EVENT_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=10_000)
DROPPED = 0
FLUSH_ROWS = 200
FLUSH_SECONDS = 1.0


async def writer_loop():
    buf = []
    while True:
        try:
            item = await asyncio.wait_for(EVENT_QUEUE.get(), timeout=FLUSH_SECONDS)
            buf.extend(item)
        except asyncio.TimeoutError:
            pass
        if buf and (len(buf) >= FLUSH_ROWS or EVENT_QUEUE.empty()):
            rows, buf = buf, []
            try:
                async with async_session() as s:
                    stmt = sqlite_insert(Event).values(rows)
                    # idempotent on client event_uuid (retries are safe)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["event_uuid"])
                    await s.execute(stmt)
                    await s.commit()
            except Exception:
                log.exception("event batch insert failed (%d rows)", len(rows))


async def stats():
    async with async_session() as s:
        today = (await s.execute(
            select(func.count(Event.id)).where(func.date(Event.ts) == date.today().isoformat())
        )).scalar() or 0
    return {"queue_depth": EVENT_QUEUE.qsize(), "processed_today": today, "dropped": DROPPED}
