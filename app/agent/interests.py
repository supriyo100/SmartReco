"""Turn tracked events into the interest vector the planner gates on.

`scorer.py` has held the maths since the first commit — dual-horizon decay,
fingerprint, cosine distance — and nothing ever called it. This is the missing
half: read a user's events, join them to product categories, run the scorer,
and persist the result on `user_profiles`.

Kept separate from `scorer.py` because that module is pure and testable with
dicts; this one touches the database. The split is what lets the decay
constants be verified without a schema.

No LLM call anywhere in this file. That is the point of §5.1: the expensive
layer is gated by a cheap deterministic one, so the cheap one cannot itself be
expensive.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.agent.scorer import cosine_distance, fingerprint, score_interests
from app.db.models import Event, Product, UserProfile
from app.db.session import async_session

log = logging.getLogger("agent.interests")

# How far back the interest model looks. Beyond ~14 days the long-horizon decay
# (half-life 72h) has already reduced an event's weight below 3%, so reading
# more rows costs IO for arithmetic that rounds to nothing.
LOOKBACK_DAYS = 14
MAX_EVENTS = 500


async def load_events(user_id: int, limit: int = MAX_EVENTS) -> list[dict]:
    """This user's recent events, shaped for the scorer.

    The category comes from the product the event was about, joined here rather
    than denormalised onto `events`: a course can be recategorised, and the
    interest model should reflect the catalog as it is now, not as it was when
    the click happened.
    """
    since = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
    async with async_session() as s:
        rows = (await s.execute(
            select(Event.event_type, Event.product_id, Event.dwell_ms,
                   Event.meta, Event.ts, Product.category)
            .outerjoin(Product, Product.id == Event.product_id)
            .where(Event.user_id == user_id, Event.ts >= since)
            .order_by(Event.ts.desc())
            .limit(limit)
        )).all()

    out: list[dict] = []
    for row in rows:
        # Events with no product (a bare page_view, a search with no click)
        # carry no category and cannot move a category vector. Search text is
        # deliberately not mined for categories here — that is retrieval's job,
        # and guessing a category from a query string would put a fuzzy match
        # into a model whose whole value is that it is deterministic.
        if not row.category:
            continue
        ts = row.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append({"event_type": row.event_type, "category": row.category,
                    "ts": ts, "dwell_ms": row.dwell_ms, "meta": row.meta or {}})
    return out


async def refresh_interests(user_id: int) -> dict:
    """Recompute and persist this user's interest vectors.

    Returns a report including `cos_dist` — how far the fingerprint moved —
    which is exactly what the trigger policy needs to decide whether the
    expensive layer should run.
    """
    events = await load_events(user_id)
    vectors = score_interests(events)
    merged = vectors["merged"]

    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(user_id=user_id)
            s.add(profile)
            await s.flush()

        previous = dict(profile.interests or {})
        new_fingerprint = fingerprint(merged, profile.price_band or "",
                                      profile.stage or "")
        distance = cosine_distance(previous, merged)

        profile.interests = merged
        profile.interests_short = vectors["short"]
        profile.events_seen = len(events)
        profile.fingerprint = new_fingerprint
        await s.commit()

    return {"events": len(events), "interests": merged,
            "fingerprint": new_fingerprint, "cos_dist": distance,
            "top": next(iter(merged), "")}
