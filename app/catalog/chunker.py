"""app/catalog/chunker.py — turns a curated course into Chroma chunks
(data/COURSE_SCHEMA.md §0, arch D2 Tier 2).

Design rules enforced here:
  * objective chunks are the highest-value retrieval keys (user-intent phrasing)
  * module GROUPS, not individual modules (short titles embed poorly, and 28
    chunks from one course would dominate RRF and starve the rest of the catalog)
  * price / schedule / mentors / perks are NEVER embedded — they are persuasion
    facts (T3), injected at generate time instead
  * every chunk carries parent_id; retrieval dedupes to parent

Promoted from Experiment/chunker.py, with the freshness fields added to chunk
metadata so fusion_rank can read them without a second lookup.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

from app.catalog.freshness import score_freshness


def chunk_metadata(p: dict, product_id: int, today: date | None = None) -> dict:
    """T1 filter surface, attached to every chunk of this course.

    Chroma rejects None in metadata, so unknown values are OMITTED rather than
    sentinel-encoded. For `price` that is a deliberate, documented consequence
    (schema §2): a course whose price we don't know cannot satisfy an "under
    ₹5000" filter, and `price_band` carries band-level matching in its place.
    """
    fresh = score_freshness(p, today)
    meta = {
        "parent_id": product_id,
        "slug": p["slug"],
        "category": p["category"],
        "level": p["level"],
        "is_free": p.get("is_free", False),
        "is_active": p.get("is_active", True),
        # --- freshness (schema §6) — precomputed so ranking never re-parses dates
        "mode": fresh["mode"],
        "freshness": fresh["score"],
        "enrollment_status": fresh["enrollment_status"],
    }
    if p.get("price") is not None:
        meta["price"] = p["price"]
    if p.get("price_band"):
        meta["price_band"] = p["price_band"]
    if p.get("rating") is not None:
        meta["rating"] = p["rating"]
    return meta


def build_chunks(p: dict, product_id: int, today: date | None = None) -> Iterator[dict]:
    base_meta = chunk_metadata(p, product_id, today)

    # --- 1. overview chunk (broad / vague queries) -------------------------
    yield {
        "id": f"{product_id}:overview",
        "text": (f"{p['title']}. {p['overview']} "
                 f"Skills covered: {', '.join(p.get('skills', []))}."),
        "metadata": {**base_meta, "chunk_type": "overview"},
    }

    # --- 2. objective chunks (best retrieval keys) -------------------------
    for i, obj in enumerate(p.get("objectives", [])):
        yield {
            "id": f"{product_id}:obj:{i}",
            "text": f"{p['title']} — learning objective: {obj}",
            "metadata": {**base_meta, "chunk_type": "objective"},
        }

    # --- 3. module-group chunks (specific Proof-stage citations) -----------
    for i, g in enumerate(p.get("module_groups", [])):
        yield {
            "id": f"{product_id}:mod:{i}",
            "text": (f"{p['title']} — {g['group']}. "
                     f"Modules: {'; '.join(g['modules'])}."),
            "metadata": {**base_meta, "chunk_type": "module_group",
                         "group_name": g["group"]},
        }

    # --- 4. projects chunk (only if present) ------------------------------
    if p.get("projects"):
        yield {
            "id": f"{product_id}:projects",
            "text": f"{p['title']} — hands-on projects: {'; '.join(p['projects'])}.",
            "metadata": {**base_meta, "chunk_type": "projects"},
        }

    # NOT embedded, by design: format, mentors, perks, price string,
    # cohort_start / cohort_start_stale, career_roles (career_roles are a card
    # field, and embedding them would make every course match every role query).
    # objectives_dropped is likewise never embedded — it is the record of claims
    # we refused to stand behind.


def generate_context(p: dict, matched_chunks: list[dict], user_skills: set,
                     today: date | None = None) -> str:
    """Tier 3 — compressed injection, ~130 tokens/candidate (arch D2).

    Ships the top-3 matched chunks only, plus persuasion facts. Retrieval already
    told us which parts matched; sending the rest is waste.

    Price and freshness are rendered through the schema's honesty rules rather
    than formatted blindly: a null price becomes its price_note, and the
    freshness line comes from freshness_label(), which cannot emit a past date.
    """
    hits = "; ".join(c["metadata"].get("group_name") or c["text"][:80]
                     for c in matched_chunks[:3])
    overlap = ", ".join(sorted(set(p.get("skills", [])) & user_skills)[:6])
    f = p.get("format", {})
    fresh = score_freshness(p, today)

    price = (f"₹{p['price']:,.0f}" if p.get("price") is not None
             else p.get("price_note", "pricing at checkout"))
    duration = f.get("duration") or (f"{f['duration_hours']}h"
                                     if f.get("duration_hours") else "")

    return (
        f"[{p['slug']}] {p['title']} — {price}, {f.get('mode', '')} {duration}"
        f"{', ' + f['access'] if f.get('access') else ''}. "
        f"FRESHNESS: {fresh['label']}"
        f"{' [CLOSING SOON]' if fresh['urgency'] else ''}. "
        f"MATCHED: {hits}. "
        f"SKILLS OVERLAPPING USER INTEREST: {overlap}. "
        f"PERKS: {', '.join(p.get('perks', [])[:3])}. "
        f"NEXT: {', '.join(p.get('related_ids', [])[:2])}."
    )
