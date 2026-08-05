import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.tracking import queue as tq

router = APIRouter()


class TrackedEvent(BaseModel):
    event_uuid: str
    event_type: str
    product_id: int | None = None
    query: str | None = None
    dwell_ms: int | None = None
    meta: dict = Field(default_factory=dict)
    ts: str  # ISO from client; server re-parses defensively


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
