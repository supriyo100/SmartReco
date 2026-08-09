"""Reranking for chat retrieval — the second stage after RRF fusion.

Why a second stage at all. RRF fuses two *rankings*, and that is its strength
and its ceiling: it never looks at the query and the document together. It
knows "Chroma put this 3rd and FTS put it 7th" and nothing about whether the
course actually answers what was asked. That is fine for pulling 24 plausible
candidates out of a catalog; it is not enough to choose the three a career
advisor will name.

Three modes, behind `RERANK_MODE`, each a different point on the cost/quality
curve:

  fusion         — RRF order, unchanged. Zero extra cost. The baseline.
  cross_encoder  — deterministic lexical+structural scoring of (query, course)
                   pairs in Python. No model call, no network, ~1 ms for 24
                   candidates.
  llm            — one cheap structured call that scores the shortlist.
                   Best quality, one extra LLM call per turn.

`cross_encoder` here is a *feature-based* pair scorer, not a transformer. That
naming needs stating plainly: a real neural cross-encoder (a MiniLM trained on
MS MARCO) would mean a torch dependency, a model download, and CPU inference on
the critical path of a chat turn — for a 12-course catalog. What actually
matters about a cross-encoder is the *shape*: it scores the query jointly with
each document instead of comparing two independent rankings. This does that,
deterministically and in microseconds. Where a real one would win is semantic
paraphrase, and the vector half of the hybrid already covers that.

Every mode is deterministic except `llm`, and every mode returns the same type,
so switching is a config change rather than a code change.
"""
from __future__ import annotations

import logging
import math
import re

from app.config import settings
from app.db.models import Product

log = logging.getLogger("chat.rerank")

# Terms that carry no topical signal in this domain. Deliberately a different
# list from retrieval's _STOPWORDS: that one protects an FTS5 query from
# returning everything, this one stops "course" and "learn" from scoring a
# match on every row in the catalog.
_NOISE = {
    "course", "courses", "learn", "learning", "want", "need", "looking", "good",
    "best", "help", "should", "would", "like", "know", "get", "make", "take",
    "for", "the", "and", "with", "from", "that", "this", "have", "how", "what",
    "which", "who", "why", "when", "where", "can", "will", "about", "into",
    "job", "career", "role", "next", "start", "started", "beginner",
}

_LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}


def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#.]+", (text or "").lower())
            if len(t) > 2 and t not in _NOISE}


def _course_text(p: Product) -> str:
    """Everything about a course that is worth matching a query against.

    Tags are included twice, deliberately. They are curator-assigned topic
    labels — "langgraph", "mlops" — so a query term hitting a tag is a stronger
    signal than the same term appearing once in a 300-word description, and
    doubling is the cheapest way to say that in a bag-of-words score.
    """
    tags = " ".join(p.tags or []) if isinstance(p.tags, list) else ""
    return " ".join([p.title or "", p.category or "", tags, tags,
                     (p.description or "")[:600]])


def pair_features(query: str, p: Product) -> dict[str, float]:
    """Joint (query, course) features. This is the cross-encoder's substance.

    Returned as a dict rather than a bare score so the caller can log why one
    course outranked another — a reranker whose decisions cannot be explained
    is one nobody will trust enough to leave enabled.
    """
    q_terms = _terms(query)
    if not q_terms:
        return {"overlap": 0.0, "title_hit": 0.0, "tag_hit": 0.0,
                "phrase": 0.0, "coverage": 0.0}

    doc_terms = _terms(_course_text(p))
    title_terms = _terms(p.title or "")
    tag_terms = _terms(" ".join(p.tags or []) if isinstance(p.tags, list) else "")

    hits = q_terms & doc_terms
    # IDF-ish weighting without a corpus pass: longer, rarer-looking terms
    # ("langgraph", "kubernetes") say more about intent than short ones ("ai").
    weighted = sum(1.0 + math.log(len(t)) for t in hits)
    total = sum(1.0 + math.log(len(t)) for t in q_terms) or 1.0

    # An exact multi-word phrase match is the strongest lexical evidence there
    # is — "vector database" appearing verbatim beats both words scattered.
    q_lower = (query or "").lower()
    doc_lower = _course_text(p).lower()
    phrase = 0.0
    for n in (3, 2):
        words = re.findall(r"[a-z0-9+#.]+", q_lower)
        for i in range(len(words) - n + 1):
            gram = " ".join(words[i:i + n])
            if len(gram) > 6 and gram in doc_lower and not _terms(gram) <= _NOISE:
                phrase = 1.0
                break
        if phrase:
            break

    return {
        "overlap": weighted / total,
        "title_hit": len(q_terms & title_terms) / max(len(q_terms), 1),
        "tag_hit": len(q_terms & tag_terms) / max(len(q_terms), 1),
        "phrase": phrase,
        "coverage": len(hits) / max(len(q_terms), 1),
    }


# Weights sum to 1.0. Title and tag hits are weighted above raw overlap
# because a term in the title is a statement about what the course IS, while
# the same term in paragraph four of the description may be an aside.
_W = {"overlap": 0.34, "title_hit": 0.26, "tag_hit": 0.18,
      "phrase": 0.14, "coverage": 0.08}


def cross_encode_score(query: str, p: Product) -> float:
    feats = pair_features(query, p)
    return sum(_W[k] * v for k, v in feats.items())


def _profile_bonus(p: Product, profile_terms: set[str], level: str | None) -> float:
    """Small nudges from who is asking, not what they asked.

    Capped low (±0.08) on purpose: this is a *reranker*, and letting the user
    profile dominate would turn every answer into the same three courses
    regardless of the question. The query decides; the profile breaks ties.
    """
    bonus = 0.0
    if profile_terms:
        doc = _terms(_course_text(p))
        hit = len(profile_terms & doc) / max(len(profile_terms), 1)
        bonus += 0.05 * min(hit * 2.0, 1.0)
    if level and p.level:
        distance = abs(_LEVEL_ORDER.get(p.level.lower(), 1)
                       - _LEVEL_ORDER.get(level.lower(), 1))
        # Right level = +0.03, one step away = 0, two steps = -0.03. A beginner
        # shown an advanced course is a worse error than the reverse, but both
        # are mild — people do stretch.
        bonus += 0.03 - 0.03 * distance
    return bonus


async def rerank(query: str, candidates: list[Product], top_k: int,
                 *, profile_terms: set[str] | None = None,
                 level: str | None = None,
                 mode: str | None = None) -> tuple[list[Product], str]:
    """Reorder candidates. Returns (products, mode_actually_used).

    The returned mode is not always the requested one: `llm` falls back to
    `cross_encoder` when Mesh is unavailable, and the caller records which ran
    so a degraded turn is visible rather than silent — the same discipline the
    retrieval path uses.
    """
    mode = (mode or settings.RERANK_MODE or "fusion").lower()
    if not candidates or mode == "fusion":
        return candidates[:top_k], "fusion"

    if mode == "llm":
        ranked = await _llm_rerank(query, candidates, top_k)
        if ranked is not None:
            return ranked, "llm"
        log.info("rerank: llm unavailable, falling back to cross_encoder")
        mode = "cross_encoder"

    scored = [
        (cross_encode_score(query, p)
         + _profile_bonus(p, profile_terms or set(), level), p)
        for p in candidates
    ]
    # Stable on ties: RRF order (the input order) is the tiebreak, so a
    # zero-signal query degrades exactly to fusion rather than to arbitrary.
    order = {id(p): i for i, p in enumerate(candidates)}
    scored.sort(key=lambda sp: (-sp[0], order[id(sp[1])]))
    if log.isEnabledFor(logging.DEBUG):
        for score, p in scored[:top_k]:
            log.debug("rerank %.3f %s %s", score, p.slug, pair_features(query, p))
    return [p for _, p in scored[:top_k]], "cross_encoder"


async def _llm_rerank(query: str, candidates: list[Product],
                      top_k: int) -> list[Product] | None:
    """One structured call that scores each candidate 0-10 for this query.

    Returns None — never raises — when Mesh is off or the call fails, so the
    caller can degrade. Scoring is asked of the model rather than ordering:
    a model returning a permutation tends to drop or duplicate ids, while
    per-item scores are checked and reordered in Python.
    """
    from app.agent.providers import any_chat_provider

    if not any_chat_provider():
        return None

    listing = "\n".join(
        f"{p.id}: {p.title} | {p.category} | {p.level} | "
        f"{(p.description or '')[:settings.PROMPT_DESCRIPTION_CHARS]}"
        for p in candidates
    )
    schema = {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "score": {"type": "integer"},
                    },
                    "required": ["id", "score"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["scores"],
        "additionalProperties": False,
    }
    messages = [
        {"role": "system",
         "content": "Score how well each course answers the user's question. "
                    "0 = irrelevant, 10 = exactly what they asked for. Judge "
                    "topical fit only — ignore price, rating and popularity. "
                    "Score every id you are given, exactly once."},
        {"role": "user", "content": f"QUESTION: {query}\n\nCOURSES:\n{listing}"},
    ]

    try:
        from app.agent.mesh import structured_call

        parsed, _model, _fb = await structured_call(messages, schema, "rerank")
    except Exception as exc:
        log.warning("rerank: llm scoring failed (%s)", exc)
        return None

    by_id = {p.id: p for p in candidates}
    order = {p.id: i for i, p in enumerate(candidates)}
    scored: list[tuple[float, int, Product]] = []
    seen: set[int] = set()
    for entry in parsed.get("scores") or []:
        pid = entry.get("id")
        # An id the model invented or repeated is dropped, not trusted. Same
        # grounding discipline as the answer text itself.
        if pid in by_id and pid not in seen:
            seen.add(pid)
            scored.append((float(entry.get("score") or 0), order[pid], by_id[pid]))
    if not scored:
        return None
    # Anything the model failed to score keeps its fusion rank, below scored items.
    for pid, p in by_id.items():
        if pid not in seen:
            scored.append((-1.0, order[pid], p))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in scored[:top_k]]
