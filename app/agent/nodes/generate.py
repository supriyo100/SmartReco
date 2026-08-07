"""generate node — the narrative and the per-card copy. The ONE LLM call.

What the model is and is not asked for:

  * asked for   — the words. A hook and a reason per course, and a short
    narrative tying the set together.
  * not asked for — which courses (fusion_rank decided), what order (same),
    or the confidence number. Confidence is computed from the ranking terms,
    because a model asked for a confidence returns a number that sounds
    calibrated and is not.

This is the same division the architecture argues for throughout: deterministic
code decides, the model writes.

**The template fallback is not a degraded mode to be embarrassed about.** With
no provider reachable it produces copy from the ranking terms that actually
explain the pick — "closes 2 gaps on your resume", "matches the MLOps courses
you have been reading" — which is more specific than a lot of generated prose.
The system therefore produces recommendations with zero API keys configured,
which is what makes it demoable and testable.
"""
from __future__ import annotations

import logging

from app.db.models import Product

log = logging.getLogger("agent.generate")

SYSTEM = """You write course recommendations for a career-advice platform.

You are given a RANKED set of courses that has already been chosen for this \
user by a deterministic scorer, plus the facts that scored them. Your job is \
the words, not the selection.

RULES:
1. Never add, remove or reorder a course. Write about exactly the ones given, \
in the order given.
2. Never invent a fact. Prices, levels, titles and skills come from the data \
below and nowhere else.
3. `hook` — one sentence, max 90 characters, about why THIS person. Lead with \
their evidence: a resume gap, a category they have been reading, their stated \
goal. Not "great course for beginners".
4. `reason` — one or two sentences, max 220 characters, on what it changes for \
them. Concrete: the skill it adds, the gap it closes, the role it moves them \
toward.
5. `narrative` — 2 sentences, max 260 characters, on the set as a whole and \
the order to take it in. No greeting, no "based on your profile".
6. Plain, direct, warm. No marketing language, no exclamation marks, no \
"unlock" or "supercharge" or "dive into"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["product_id", "hook", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["narrative", "items"],
    "additionalProperties": False,
}


def confidence(terms: dict, score: float) -> float:
    """Computed, never asked of the model (§6).

    Weighted toward the terms that are *evidence about this user* — a gap their
    resume actually shows, a category they actually read — rather than toward
    the overall score, which a popular well-rated course can inflate without
    saying anything about the person.
    """
    evidence = (0.45 * terms.get("gap_match", 0.0)
                + 0.30 * terms.get("interest_match", 0.0)
                + 0.15 * terms.get("graph_adjacency", 0.0)
                + 0.10 * terms.get("level_fit", 0.0))
    # Blended with the raw score so a course with no personal evidence still
    # differs from one with none AND a poor rank.
    return round(min(0.99, max(0.05, 0.65 * evidence + 0.35 * min(score, 1.0))), 2)


def _text_of(p: Product) -> str:
    """The course text gap-matching runs against.

    Must stay identical to fusion_rank's `_text_of`, because a course scored as
    closing a gap there and described as not closing one here produces copy
    that contradicts its own ranking.
    """
    tags = " ".join(p.tags or []) if isinstance(p.tags, list) else ""
    return f"{p.title} {p.category} {tags} {(p.description or '')[:400]}".lower()


def _fmt(p: Product, terms: dict, facts: dict) -> str:
    price = "free" if not p.price else f"₹{int(p.price):,}"
    haystack = _text_of(p)
    hits = [s for s in (facts.get("missing_skills") or [])
            if s and s.lower() in haystack]
    bits = [f"id:{p.id}", p.title, f"category:{p.category}", f"level:{p.level}",
            f"price:{price}"]
    if hits:
        bits.append(f"CLOSES THESE RESUME GAPS: {', '.join(hits[:3])}")
    if terms.get("interest_match", 0) > 0.15:
        bits.append(f"matches a category they read ({p.category})")
    if terms.get("graph_adjacency", 0) >= 1.0:
        bits.append("they already viewed its prerequisite")
    if p.description:
        bits.append(f"about: {p.description[:200]}")
    return " | ".join(bits)


def _template_items(ranked: list[dict], facts: dict) -> tuple[str, list[dict]]:
    """Deterministic copy from the ranking terms. No model involved.

    Every sentence here is derived from a term that actually contributed to the
    score, so the explanation is true by construction rather than by a model
    being careful.
    """
    role = facts.get("target_role") or ""
    items = []
    for row in ranked:
        p, terms = row["product"], row["terms"]
        # Match against the course text directly. Passing an empty facts dict
        # to _fmt() was the bug: _fmt reads missing_skills FROM facts to build
        # its "CLOSES THESE GAPS" line, so with {} that line was absent and a
        # course with gap_match=1.0 got the generic "a common route into…"
        # hook — copy that contradicted its own score.
        haystack = _text_of(p)
        gaps = [s for s in (facts.get("missing_skills") or [])
                if s and s.lower() in haystack]

        if gaps:
            hook = f"Closes {', '.join(gaps[:2])} — missing from your resume."
        elif terms.get("graph_adjacency", 0) >= 1.0:
            hook = "The stated next step after a course you were reading."
        elif terms.get("interest_match", 0) > 0.15:
            hook = f"You have been reading {p.category} courses."
        elif role:
            hook = f"A common route into {role}."
        else:
            hook = f"A well-rated {p.level} course in {p.category}."

        parts = []
        if gaps:
            parts.append(f"It covers {', '.join(gaps[:3])}")
            if role:
                parts.append(f"which is what {role} postings screen for")
        else:
            parts.append(f"It is a {p.level} course in {p.category}")
        reason = ", ".join(parts) + "."
        items.append({"product_id": p.id, "hook": hook[:120],
                      "reason": reason[:260]})

    top = ranked[0]["product"].title if ranked else ""
    if facts.get("missing_skills"):
        narrative = (f"Ordered by what your resume is missing for "
                     f"{role or 'your target role'}. Start with {top}.")
    elif facts.get("cold_start"):
        narrative = ("You are new here, so this is a starting spread across the "
                     "catalog rather than a targeted set. Browse a few and it "
                     "will sharpen.")
    else:
        narrative = (f"Based on what you have been reading. Start with {top}.")
    return narrative[:300], items


async def run(user_id: int, ranked: list[dict], facts: dict) -> dict:
    """Produce narrative + per-item copy. Returns the stored `items` shape.

    Never raises: a failure here falls back to templates, because a
    recommendation set with plain copy is worth far more than an error page.
    """
    if not ranked:
        return {"narrative": "", "items": [], "model_used": "", "fallback": True}

    def _assemble(narrative: str, copy: dict, model: str, fallback: bool) -> dict:
        items = []
        for row in ranked:
            p, terms = row["product"], row["terms"]
            written = copy.get(p.id, {})
            items.append({
                "product_id": p.id,
                "rank": row["rank"],
                "hook": (written.get("hook") or "")[:160],
                "reason": (written.get("reason") or "")[:400],
                "confidence": confidence(terms, row["score"]),
                # The terms that produced the score, stored with the card. This
                # is what makes a recommendation auditable after the fact —
                # "why was this ranked first" is answerable from the row.
                "terms": {k: round(v, 3) for k, v in terms.items()},
                "next_step_id": None,
            })
        return {"narrative": narrative, "items": items,
                "model_used": model, "fallback": fallback}

    from app.agent.providers import any_chat_provider

    if not any_chat_provider():
        narrative, written = _template_items(ranked, facts)
        return _assemble(narrative, {w["product_id"]: w for w in written},
                         "template", True)

    catalog = "\n".join(_fmt(row["product"], row["terms"], facts) for row in ranked)
    profile_lines = []
    if facts.get("target_role"):
        profile_lines.append(f"Target role: {facts['target_role']}")
    if facts.get("experience_years") is not None:
        profile_lines.append(f"Experience: {facts['experience_years']} years")
    if facts.get("missing_skills"):
        profile_lines.append("Resume gaps: " + ", ".join(facts["missing_skills"][:8]))
    if facts.get("interests"):
        profile_lines.append("Reads about: " + ", ".join(list(facts["interests"])[:4]))
    if facts.get("goals"):
        profile_lines.append(f"Their stated goal: {facts['goals'][:200]}")
    if facts.get("budget_max"):
        profile_lines.append(f"Budget: at most ₹{int(facts['budget_max']):,}")

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",
         "content": "THIS USER:\n" + ("\n".join(profile_lines) or "Nothing known yet.")
                    + f"\n\nTHE RANKED SET (write about exactly these, in this "
                      f"order):\n{catalog}"},
    ]

    try:
        from app.agent.mesh import writer_call

        parsed, model = await writer_call(messages, SCHEMA)
    except Exception as exc:
        log.warning("generate: model call failed (%s) — using templates",
                    type(exc).__name__)
        narrative, written = _template_items(ranked, facts)
        return _assemble(narrative, {w["product_id"]: w for w in written},
                         "template", True)

    # The model may only write about ids it was given. An invented id is
    # dropped here rather than rendered as a card that 404s — the same
    # guarantee the chat path enforces on citations.
    allowed = {row["product"].id for row in ranked}
    copy = {item["product_id"]: item for item in (parsed.get("items") or [])
            if item.get("product_id") in allowed}
    if len(copy) < len(ranked):
        log.info("generate: model wrote copy for %d of %d courses; "
                 "templating the rest", len(copy), len(ranked))
        _, written = _template_items(ranked, facts)
        for item in written:
            copy.setdefault(item["product_id"], item)

    return _assemble((parsed.get("narrative") or "")[:400], copy, model, False)
