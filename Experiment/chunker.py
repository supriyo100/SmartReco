"""app/catalog/chunker.py — turns a seed product into Chroma chunks (arch D2, Tier 2).

Design rules enforced here:
  * objective chunks are the highest-value retrieval keys (user-intent phrasing)
  * module GROUPS, not individual modules (short titles embed poorly; 28 chunks
    from one course would dominate RRF and starve the rest of the catalog)
  * price / schedule / mentors / perks are NEVER embedded — persuasion facts,
    injected at generate time instead
  * every chunk carries parent_id; retrieval dedupes to parent
"""
from typing import Iterator


def build_chunks(p: dict, product_id: int) -> Iterator[dict]:
    base_meta = {
        "parent_id": product_id,
        "category": p["category"],
        "level": p["level"],
        "price": p["price"],
        "is_free": p["is_free"],
        "is_active": p.get("is_active", True),
    }

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
    # cohort_start_stale, career_roles (career_roles are a card field, and
    # embedding them would make every course match every role query).


def generate_context(p: dict, matched_chunks: list[dict], user_skills: set) -> str:
    """Tier 3 — compressed injection, ~120 tokens/candidate (arch D2).

    Ships the top-3 matched chunks only, plus persuasion facts. Retrieval already
    told us which parts matched; sending the rest is waste.
    """
    hits = "; ".join(c["metadata"].get("group_name") or c["text"][:80]
                     for c in matched_chunks[:3])
    overlap = ", ".join(sorted(set(p.get("skills", [])) & user_skills)[:6])
    f = p.get("format", {})
    return (
        f"[{p['slug']}] {p['title']} — ₹{p['price']}, {f.get('mode','')} "
        f"{f.get('duration','')}, {f.get('access','')}. "
        f"MATCHED: {hits}. "
        f"SKILLS OVERLAPPING USER INTEREST: {overlap}. "
        f"PERKS: {', '.join(p.get('perks', [])[:3])}. "
        f"NEXT: {', '.join(p.get('related_ids', [])[:2])}."
    )
