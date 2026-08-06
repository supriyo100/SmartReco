import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from app.tracking import queue as tq

router = APIRouter()


class TrackedEvent(BaseModel):
    event_uuid: str
    event_type: str
    product_id: int | None = None
    query: str | None = None
    dwell_ms: int | None = None
    meta: dict = Field(default_factory=dict)
    # ISO string from the client, parsed here into a datetime. It must be a
    # datetime by the time it reaches the writer: events.ts is a DateTime
    # column, and SQLite's driver rejects a str outright — which silently cost
    # us every event until the batch insert was actually exercised.
    ts: datetime

    @field_validator("ts", mode="after")
    @classmethod
    def _naive_utc(cls, v: datetime) -> datetime:
        """Client clocks send tz-aware ISO ('...Z'); every other ts in the
        schema is a naive UTC datetime.utcnow(). Normalize, or comparisons
        between the two raise."""
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v


class EventBatch(BaseModel):
    events: list[TrackedEvent]


@router.post("/api/events", status_code=202)
async def ingest(batch: EventBatch, request: Request):
    sid = request.cookies.get("sid", "anon")
    user_id = getattr(request.state, "user_id", None)
    rows = [{**e.model_dump(), "session_id": sid, "user_id": user_id} for e in batch.events]
    batch_id = str(uuid.uuid4())
    try:
        tq.EVENT_QUEUE.put_nowait(rows)
    except Exception:
        tq.DROPPED += len(rows)
    return {"accepted": len(rows), "batch_id": batch_id}


@router.get("/api/events/stats")  # ▲A12
async def event_stats():
    return await tq.stats()
