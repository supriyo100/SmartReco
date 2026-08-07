"""Hybrid catalog retrieval for the chat agent (arch §3.1).

Two retrievers over the same catalog, fused by Reciprocal Rank Fusion: Chroma
for semantic similarity, FTS5 for the exact title and technology-name matches
semantic search softens. RRF because it needs no score calibration between two
retrievers whose scores are not comparable.

The property that matters most here is the one §0 calls grounding: the chat
answer may only name courses that came back from this function. That is what
makes "the model cannot invent a course" true of the chatbot and not just of
the recommendation cards.

Degradation is deliberate and ordered. Vector search needs both Chroma and a
Mesh key; FTS5 needs neither. If the vector half is unavailable — no key, no
collection, a 402 — retrieval quietly falls back to FTS5 alone rather than
failing the request. A keyword-only answer is worse than a hybrid one and far
better than an error, and the caller is told which path ran so the degradation
is visible rather than silent.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select, text

from app.config import settings
from app.db.models import Product
from app.db.session import async_session

log = logging.getLogger("chat.retrieval")

RRF_K = 60          # standard RRF damping; rank 1 → 1/61, rank 10 → 1/70
CANDIDATES = 24     # per retriever, before fusion
DEFAULT_TOP_K = 6
# Candidates carried from fusion into reranking. A reranker given exactly as
# many candidates as it returns cannot change anything, so the pool has to be
# wider than top_k — 16 is roughly the whole catalog today and still bounded
# for when it isn't.
RERANK_POOL = 16

# FTS5 treats these as query syntax. A user asking "what's the difference
# between RAG and fine-tuning?" would otherwise raise a parse error on the
# apostrophe and return nothing.
_FTS_SPECIAL = re.compile(r"""["'()*:^\-]""")
_STOPWORDS = {
    "what", "which", "who", "how", "why", "when", "where", "is", "are", "the",
    "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "about",
    "should", "would", "could", "can", "do", "does", "i", "me", "my", "you",
    "your", "best", "good", "any", "some", "please", "tell", "show", "help",
    "want", "need", "looking", "course", "courses", "recommend", "suggest",
}


def fts_query(raw: str) -> str:
    """Turn free-text into a safe FTS5 OR-query.

    Quoting each token individually makes every one a literal, so no user input
    can be interpreted as FTS5 operators. OR rather than AND because a chat
    message is a sentence, not a search box — requiring every token to appear
    would return nothing for almost any real question.
    """
    cleaned = _FTS_SPECIAL.sub(" ", raw or "")
    tokens = [t for t in re.findall(r"\w+", cleaned.lower())
              if len(t) > 2 and t not in _STOPWORDS]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:12])


async def _fts_search(query: str, limit: int = CANDIDATES) -> list[int]:
    q = fts_query(query)
    if not q:
        return []
    async with async_session() as s:
        try:
            rows = (await s.execute(
                text("SELECT rowid FROM products_fts WHERE products_fts MATCH :q "
                     "ORDER BY rank LIMIT :n"),
                {"q": q, "n": limit},
            )).scalars().all()
        except Exception as exc:
            # A malformed FTS query must return nothing, not 500 the chat.
            log.warning("FTS query failed (%s); falling back to no keyword hits", exc)
            return []
    return list(rows)


async def _vector_search(query: str, limit: int = CANDIDATES) -> list[int]:
    """Chroma similarity → product ids, deduped to parent (arch §2).

    Chunks are per-objective and per-module-group, so one course can occupy
    several of the top hits. Deduping to `parent_id` while preserving order is
    what stops a single well-chunked course from filling the whole result set.
    """
    if not settings.can_embed:
        return []
    try:
        from app.agent.mesh import embed_batch
        from app.catalog.vectors import get_collection

        # is_query=True matters for the local backend: nomic-embed is
        # asymmetric and expects "search_query:" here against the
        # "search_document:" prefix used at ingest. Getting this wrong is a
        # silent relevance loss, not an error.
        vector = (await embed_batch([query], is_query=True))[0]
        result = get_collection().query(
            query_embeddings=[vector],
            n_results=limit,
            where={"is_active": True},
            include=["metadatas"],
        )
    except Exception as exc:
        log.warning("vector search unavailable (%s); using FTS5 only", exc)
        return []

    ids: list[int] = []
    for meta in (result.get("metadatas") or [[]])[0]:
        pid = meta.get("parent_id") or meta.get("product_id")
        if isinstance(pid, (int, float)) and int(pid) not in ids:
            ids.append(int(pid))
    return ids


def _rrf(ranked_lists: list[list[int]]) -> list[int]:
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, pid in enumerate(ranked, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank)
    return [pid for pid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


async def retrieve(query: str, top_k: int = DEFAULT_TOP_K,
                   max_price: float | None = None,
                   *, rerank_query: str | None = None,
                   profile_terms: set[str] | None = None,
                   level: str | None = None,
                   rerank_mode: str | None = None) -> tuple[list[Product], str]:
    """Hybrid retrieve, then rerank. Returns (products, path).

    `max_price` is a metadata filter applied in SQL rather than in the prompt:
    a budget the user stated is a fact about what they can buy, and asking a
    model to respect it politely is not the same as not returning it.

    Two stages, because they answer different questions. RRF fuses two
    *rankings* and never sees the query and the document together — good for
    pulling plausible candidates, not good enough for choosing the three a
    career advisor will name. The reranker scores (query, course) jointly over
    a widened candidate pool. See `app/chat/rerank.py`.

    `rerank_query` exists because the two stages want different text. Recall
    benefits from the expanded query (message + target role + recent turns);
    precision suffers from it, since every extra term dilutes the overlap
    signal. So retrieval widens and reranking scores against the user's actual
    question.
    """
    query = (query or "").strip()
    if not query:
        return [], "empty"

    vector_ids = await _vector_search(query)
    fts_ids = await _fts_search(query)

    lists = [ids for ids in (vector_ids, fts_ids) if ids]
    path = ("hybrid" if vector_ids and fts_ids
            else "vector" if vector_ids
            else "fts" if fts_ids else "none")
    if not lists:
        return [], path

    # Widened pool: reranking only helps if it is given more than it returns.
    # RERANK_POOL rather than top_k*3 so the pool does not shrink when a caller
    # asks for a small top_k — the reranker's job is to find the good ones.
    ordered = _rrf(lists)[:RERANK_POOL]
    async with async_session() as s:
        stmt = select(Product).where(Product.id.in_(ordered),
                                     Product.is_active.is_(True))
        if max_price is not None:
            stmt = stmt.where(Product.price <= max_price)
        found = list((await s.execute(stmt)).scalars().all())

    # Restore fusion order, which the IN() query does not preserve.
    position = {pid: i for i, pid in enumerate(ordered)}
    found.sort(key=lambda p: position.get(p.id, 1 << 30))

    from app.chat.rerank import rerank

    ranked, used = await rerank(rerank_query or query, found, top_k,
                                profile_terms=profile_terms, level=level,
                                mode=rerank_mode)
    return ranked, f"{path}+{used}" if used != "fusion" else path


async def fallback_catalog(top_k: int = DEFAULT_TOP_K) -> list[Product]:
    """Highest-rated active courses, for when retrieval returns nothing.

    Used only so a chat answer about an off-catalog topic can still ground any
    course it names in real rows. It is explicitly labelled as a fallback in
    the prompt so the model does not present it as a targeted match.
    """
    async with async_session() as s:
        return list((await s.execute(
            select(Product).where(Product.is_active.is_(True))
            .order_by(Product.rating.desc()).limit(top_k)
        )).scalars().all())
