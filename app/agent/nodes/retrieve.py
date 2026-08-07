"""retrieve node — turn what we know about a user into candidate courses.

Multi-query, not one blended query (arch §3.1). A user interested in both RAG
and MLOps has an average vector that points at neither, so a single query
returns the courses nearest that meaningless midpoint. Running one query per
signal and fusing the ranked lists with RRF keeps both interests represented —
this is the design the `two_interests` eval persona exists to check.

Query sources, in descending trust:

  1. ATS gaps — the most specific thing we have. "Missing langgraph for
     Agentic AI Engineer" is a retrieval query that finds the exact course
     that closes it.
  2. Target role and stated goals — what they say they want.
  3. Behavioral categories — what they actually read.
  4. Declared skills — weakest, because a skill they already have is a reason
     NOT to recommend a course about it. Used only to break ties, never alone.

Retrieval is hybrid (Chroma + FTS5) and reuses `app.chat.retrieval`, so the
recommendation surface and the chat advisor cannot drift apart in what they
consider retrievable.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import Product, ResumeAnalysis, UserProfile
from app.db.session import async_session

log = logging.getLogger("agent.retrieve")

# Per-query candidates, and the ceiling on how many queries we run. Each query
# is one retrieval round; the cap is what keeps a rich profile from turning
# into a dozen round trips.
PER_QUERY = 8
MAX_QUERIES = 5


async def build_queries(user_id: int) -> tuple[list[str], dict]:
    """The queries to run, plus the facts the ranker will need.

    Returned together because both come from the same three rows, and reading
    them twice would double the DB work on the agent's critical path.
    """
    async with async_session() as s:
        profile = (await s.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )).scalar_one_or_none()
        ats = (await s.execute(
            select(ResumeAnalysis)
            .where(ResumeAnalysis.user_id == user_id,
                   ResumeAnalysis.is_current.is_(True))
            .order_by(ResumeAnalysis.created_at.desc())
        )).scalars().first()

    facts = {
        "target_role": (getattr(profile, "target_role", "") or "").strip(),
        "goals": (getattr(profile, "goals", "") or "").strip(),
        "skills": list(getattr(profile, "skills", []) or []),
        "interests": dict(getattr(profile, "interests", {}) or {}),
        "budget_max": getattr(profile, "budget_max", None),
        "experience_years": getattr(profile, "experience_years", None),
        "missing_skills": list(getattr(ats, "missing_skills", []) or []),
        "ats_score": getattr(ats, "ats_score", None),
        "ats_role": (getattr(ats, "target_role", "") or ""),
        "events_seen": getattr(profile, "events_seen", 0) or 0,
    }

    queries: list[str] = []

    # 1. The gap list, in pairs. One query per skill would burn the budget on
    #    near-duplicates; pairing keeps each query specific while covering more
    #    of the list.
    gaps = facts["missing_skills"][:6]
    for i in range(0, min(len(gaps), 4), 2):
        pair = " ".join(gaps[i:i + 2])
        queries.append(f"{pair} {facts['target_role']}".strip())

    # 2. What they said they want.
    if facts["target_role"]:
        queries.append(f"{facts['target_role']} course")
    if facts["goals"]:
        queries.append(facts["goals"][:180])

    # 3. What they actually read — top two categories, not all five, because
    #    the tail of a decayed vector is noise.
    for category in list(facts["interests"])[:2]:
        queries.append(category)

    # Dedupe, keeping order, and drop anything too short to retrieve on.
    seen, unique = set(), []
    for q in queries:
        key = q.lower().strip()
        if len(key) > 3 and key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:MAX_QUERIES], facts


async def run(user_id: int) -> tuple[list[Product], dict]:
    """Retrieve candidates for one user. Returns (products, facts).

    Falls back to the catalog when a profile is too thin to generate a single
    query — a brand-new account with no resume, no role and no browsing. That
    is the cold-start case (§5.2), and the caller marks the result so the
    narrative never claims a targeted match it cannot justify.
    """
    from app.chat.retrieval import fallback_catalog, retrieve

    queries, facts = await build_queries(user_id)
    facts["queries"] = queries

    if not queries:
        facts["cold_start"] = True
        return await fallback_catalog(top_k=8), facts

    # RRF over the per-query result lists. Reusing chat retrieval means the
    # reranker, the budget filter and the degradation path are all shared.
    ranked: dict[int, float] = {}
    by_id: dict[int, Product] = {}
    paths: list[str] = []

    for query in queries:
        products, path = await retrieve(query, top_k=PER_QUERY,
                                        max_price=facts.get("budget_max"),
                                        rerank_mode="fusion")
        paths.append(path)
        for rank, product in enumerate(products, start=1):
            by_id[product.id] = product
            # Standard RRF damping. Fused across QUERIES here, where
            # chat/retrieval fuses across RETRIEVERS — the same operator
            # applied at a different level.
            ranked[product.id] = ranked.get(product.id, 0.0) + 1.0 / (60 + rank)

    facts["retrieval_paths"] = paths
    facts["rrf"] = ranked
    if not ranked:
        facts["cold_start"] = True
        return await fallback_catalog(top_k=8), facts

    ordered = sorted(ranked, key=lambda pid: -ranked[pid])
    return [by_id[pid] for pid in ordered], facts
