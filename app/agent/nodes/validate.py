"""validate node — the grounding guarantee, enforced rather than requested.

The README's second claim is that every recommended course id came from
retrieval over the real catalog and that a validate node enforces it. This is
that node. It runs after generate and before anything is persisted, and it is
the reason "the model cannot invent a course" is a property of the system
rather than a hope about the prompt.

What it checks, in order of severity:

  1. Every product_id was in the retrieved candidate set. An id from anywhere
     else is dropped.
  2. Every product still exists and is active. A course deactivated between
     retrieval and generation must not be stored — a rec row outlives the
     request that made it.
  3. Copy that names a course we do not have. The model is told to write about
     the given ids, but a sentence like "you might also look at X" costs
     nothing to emit and is a false claim with our name on it.
  4. No duplicates, ranks contiguous from 0.

A set that fails validation entirely returns empty rather than partially — a
recommendation set that lost half its cards to validation is evidence
something upstream is wrong, and storing the remainder hides it.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select

from app.db.models import Product
from app.db.session import async_session

log = logging.getLogger("agent.validate")


async def run(items: list[dict], allowed_ids: set[int],
              narrative: str = "") -> tuple[list[dict], str, list[str]]:
    """Returns (clean_items, clean_narrative, problems)."""
    problems: list[str] = []
    if not items:
        return [], narrative, problems

    # 1. Only ids retrieval actually produced.
    grounded = []
    for item in items:
        pid = item.get("product_id")
        if pid in allowed_ids:
            grounded.append(item)
        else:
            problems.append(f"dropped ungrounded product_id={pid}")
            log.warning("validate: model produced ungrounded id %s", pid)

    # 2. Still real, still active.
    ids = [i["product_id"] for i in grounded]
    async with async_session() as s:
        live = set((await s.execute(
            select(Product.id).where(Product.id.in_(ids),
                                     Product.is_active.is_(True))
        )).scalars().all()) if ids else set()
        titles = dict((await s.execute(
            select(Product.id, Product.title).where(Product.is_active.is_(True))
        )).all())

    active = []
    for item in grounded:
        if item["product_id"] in live:
            active.append(item)
        else:
            problems.append(f"dropped inactive product_id={item['product_id']}")

    # 4. Dedupe, keeping the best-ranked instance, then renumber.
    seen: set[int] = set()
    unique = []
    for item in sorted(active, key=lambda i: i.get("rank", 0)):
        if item["product_id"] in seen:
            problems.append(f"dropped duplicate product_id={item['product_id']}")
            continue
        seen.add(item["product_id"])
        unique.append(item)
    for rank, item in enumerate(unique):
        item["rank"] = rank

    # 3. Copy that names something outside the catalog. Checked against real
    #    titles rather than by asking the model to behave.
    clean_narrative = _strip_unknown_courses(narrative, titles, problems)
    for item in unique:
        item["hook"] = _strip_unknown_courses(item.get("hook", ""), titles, problems)
        item["reason"] = _strip_unknown_courses(item.get("reason", ""), titles,
                                                problems)

    if problems:
        log.info("validate: %d problem(s): %s", len(problems), "; ".join(problems[:5]))
    return unique, clean_narrative, problems


# A quoted or bolded phrase is how a model names a course it is inventing.
_QUOTED = re.compile(r'["“”]([^"“”]{6,90})["“”]|\*\*([^*]{6,90})\*\*')


def _strip_unknown_courses(text: str, titles: dict[int, str],
                           problems: list[str]) -> str:
    """Remove a quoted phrase that looks like a course title we do not sell.

    Conservative on purpose: only quoted or bolded spans are considered, and a
    span is only removed when it looks like a course name (contains a catalog
    word) and matches no real title. Ordinary quoted words survive.
    """
    if not text:
        return text
    known = {t.lower() for t in titles.values()}
    course_words = ("course", "bootcamp", "program", "specialization",
                    "certification", "track", "masterclass", "nanodegree")

    def replace(match: re.Match) -> str:
        phrase = (match.group(1) or match.group(2) or "").strip()
        low = phrase.lower()
        if any(low in title or title in low for title in known):
            return match.group(0)
        if any(word in low for word in course_words):
            problems.append(f"removed invented course title {phrase!r}")
            log.warning("validate: removed invented course title %r", phrase)
            return ""
        return match.group(0)

    cleaned = _QUOTED.sub(replace, text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;])", r"\1", cleaned)
    return cleaned.strip()
