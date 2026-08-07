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
                # Count them: without this, a batch that fails to insert is
                # invisible — the handler already returned 202 — and /stats
                # reports a healthy queue while events silently vanish.
                global DROPPED
                DROPPED += len(rows)
                log.exception("event batch insert failed (%d rows)", len(rows))
                continue

            # Behavior is the project's headline trigger (§5.2): what someone
            # reads should change what they are shown. Consulted here, after
            # the write, so the planner sees the events it is judging.
            #
            # `maybe_generate` is the planner, not the generator — it applies
            # the debounce and thresholds, so calling it once per flush costs a
            # few indexed counts, not an LLM call. Scheduled as a task so a
            # generation never delays the writer draining its queue.
            for user_id in {r.get("user_id") for r in rows if r.get("user_id")}:
                asyncio.create_task(_consider(user_id))


async def _consider(user_id: int) -> None:
    """Ask the planner whether this user's behavior justifies a new set."""
    from app.agent.graph import maybe_generate

    try:
        await maybe_generate(user_id, reason_hint="")
    except Exception:
        # Never let a recommendation failure affect event ingestion — tracking
        # is the load-bearing path and must not depend on the agent.
        log.exception("behavioral recommendation trigger failed (user_id=%s)",
                      user_id)


async def stats():
    async with async_session() as s:
        today = (await s.execute(
            select(func.count(Event.id)).where(func.date(Event.ts) == date.today().isoformat())
        )).scalar() or 0
    return {"queue_depth": EVENT_QUEUE.qsize(), "processed_today": today, "dropped": DROPPED}
