"""fusion_rank node — deterministic weighted ranking (arch §3, ▲A14+B2).

Every term here is computed in Python. Not one is asked of a model, and that is
the same argument `ats.py` makes about its score: a model asked to rank returns
a plausible order that changes between runs on identical input, and a user who
sees their recommendations reshuffle without doing anything has learned the
system is arbitrary.

    0.40 · rrf              how strongly retrieval surfaced it
    0.22 · gap_match        does it close a gap the ATS actually found
    0.16 · interest_match   does it match what they actually read
    0.10 · level_fit        is it the right difficulty for their experience
    0.07 · rating_prior     catalog quality
    0.05 · graph_adjacency  prereq/related edges from courses they viewed
    ────
    1.00

`gap_match` at 0.22 is the change from the original spec, which had no such
term. It is the single most defensible reason to recommend a course to someone
who uploaded a resume: the gap is evidence, not inference. That weight comes
out of the freshness term, which cannot be computed at all until the catalog
carries live cohort dates (§6) — a term that is always 0.5 is not a term.

Two hard constraints after scoring, both from ▲B3/B4:
  * exclude anything already converted — recommending a course someone bought
    is the most visible possible failure
  * at most 2 of the final 5 from one category, so a strong interest in one
    area cannot produce five near-identical cards
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import Event, Product, Recommendation
from app.db.session import async_session

log = logging.getLogger("agent.rank")

WEIGHTS = {
    "rrf": 0.40,
    "gap_match": 0.22,
    "interest_match": 0.16,
    "level_fit": 0.10,
    "rating_prior": 0.07,
    "graph_adjacency": 0.05,
}

# No more than this many from one category in the final set. Two, not three:
# with a 12-course catalog three from one category is nearly the whole
# category, and the point of the cap is that a set should show a person
# something they would not have found themselves.
CATEGORY_CAP = 2

# Penalty for a course that was in the user's previous set. Not an exclusion —
# a course can legitimately still be the best answer — but a set that never
# changes is indistinguishable from a broken one.
REPEAT_PENALTY = 0.15

_LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}


def _expected_level(years: int | None) -> str:
    if years is None:
        return "beginner"
    return "beginner" if years < 2 else "intermediate" if years < 6 else "advanced"


def _text_of(p: Product) -> str:
    tags = " ".join(p.tags or []) if isinstance(p.tags, list) else ""
    return f"{p.title} {p.category} {tags} {(p.description or '')[:400]}".lower()


def gap_match(p: Product, missing: list[str]) -> float:
    """Fraction of the user's missing skills this course covers.

    Capped at 3 hits: a course covering three gaps is excellent, and one
    claiming to cover ten is a course whose description is a keyword list.
    """
    if not missing:
        return 0.0
    text = _text_of(p)
    hits = sum(1 for skill in missing if skill and skill.lower() in text)
    return min(hits, 3) / 3.0


def interest_match(p: Product, interests: dict) -> float:
    """The user's own decayed weight for this course's category."""
    if not interests:
        return 0.0
    return float(interests.get(p.category, 0.0))


def level_fit(p: Product, years: int | None) -> float:
    """1.0 at the right level, 0.5 one step away, 0.0 two.

    Not a hard filter: people do stretch, and a beginner who is ready for an
    intermediate course should still see it — just below the ones that fit.
    """
    expected = _LEVEL_ORDER[_expected_level(years)]
    actual = _LEVEL_ORDER.get((p.level or "").lower(), 1)
    return max(0.0, 1.0 - 0.5 * abs(expected - actual))


def rating_prior(p: Product) -> float:
    """Rating on 0-1, with an unrated course treated as average rather than
    as bad. Half the catalog publishes no rating, and scoring those 0.0 would
    bury them for a fact about our data rather than about the course."""
    if not p.rating:
        return 0.5
    return max(0.0, min(1.0, (float(p.rating) - 3.0) / 2.0))


def graph_adjacency(p: Product, viewed_slugs: set[str]) -> float:
    """Is this the stated next step from something they looked at?

    The catalog's own `prereq_ids` / `related_ids` edges — curator-asserted, not
    inferred — so this term is as trustworthy as the catalog itself.
    """
    if not viewed_slugs:
        return 0.0
    prereqs = set(p.prereq_ids or []) if isinstance(p.prereq_ids, list) else set()
    related = set(p.related_ids or []) if isinstance(p.related_ids, list) else set()
    if prereqs & viewed_slugs:
        return 1.0          # they viewed its prerequisite: this is next
    if related & viewed_slugs:
        return 0.6
    return 0.0


async def _user_context(user_id: int) -> tuple[set[int], set[str], set[int]]:
    """(converted product ids, viewed slugs, previously recommended ids)."""
    async with async_session() as s:
        converted = set((await s.execute(
            select(Event.product_id).where(Event.user_id == user_id,
                                           Event.event_type == "conversion",
                                           Event.product_id.is_not(None))
        )).scalars().all())

        viewed_ids = set((await s.execute(
            select(Event.product_id)
            .where(Event.user_id == user_id, Event.product_id.is_not(None))
            .order_by(Event.ts.desc()).limit(60)
        )).scalars().all())
        viewed_slugs = set()
        if viewed_ids:
            viewed_slugs = set((await s.execute(
                select(Product.slug).where(Product.id.in_(viewed_ids))
            )).scalars().all())

        previous = (await s.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user_id,
                   Recommendation.is_current.is_(True))
            .order_by(Recommendation.created_at.desc())
        )).scalars().first()
        previous_ids = {item.get("product_id") for item in (previous.items or [])
                        } if previous else set()

    return converted, viewed_slugs, previous_ids


async def run(user_id: int, candidates: list[Product], facts: dict,
              top_k: int = 5) -> list[dict]:
    """Score, filter and diversify. Returns [{product, score, terms}] ranked."""
    if not candidates:
        return []

    converted, viewed_slugs, previous_ids = await _user_context(user_id)
    rrf = facts.get("rrf") or {}
    rrf_max = max(rrf.values()) if rrf else 1.0
    missing = facts.get("missing_skills") or []
    interests = facts.get("interests") or {}
    years = facts.get("experience_years")

    scored: list[dict] = []
    for p in candidates:
        # A course they already bought is never a recommendation. This is an
        # exclusion, not a penalty — no score can justify it.
        if p.id in converted:
            continue

        terms = {
            "rrf": (rrf.get(p.id, 0.0) / rrf_max) if rrf_max else 0.0,
            "gap_match": gap_match(p, missing),
            "interest_match": interest_match(p, interests),
            "level_fit": level_fit(p, years),
            "rating_prior": rating_prior(p),
            "graph_adjacency": graph_adjacency(p, viewed_slugs),
        }
        score = sum(WEIGHTS[k] * v for k, v in terms.items())
        if p.id in previous_ids:
            score -= REPEAT_PENALTY
        scored.append({"product": p, "score": score, "terms": terms})

    scored.sort(key=lambda row: -row["score"])

    # Diversity cap, applied greedily down the ranked list so the best course
    # in each category always survives it.
    final: list[dict] = []
    per_category: dict[str, int] = {}
    overflow: list[dict] = []
    for row in scored:
        category = row["product"].category or ""
        if per_category.get(category, 0) >= CATEGORY_CAP:
            overflow.append(row)
            continue
        per_category[category] = per_category.get(category, 0) + 1
        final.append(row)
        if len(final) == top_k:
            break

    # A catalog with few categories can leave the set short. Backfill from the
    # overflow rather than returning two cards — an under-full set reads as a
    # bug, and the cap is a preference, not a correctness constraint.
    if len(final) < top_k:
        final.extend(overflow[:top_k - len(final)])

    for rank, row in enumerate(final):
        row["rank"] = rank
    return final
