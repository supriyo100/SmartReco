"""Learning-path flowchart — the diagram that ships with a chat answer.

The flowchart is built in Python from `prereq_ids` and `related_ids` on the
retrieved courses, never authored by the model. That is the same decision the
validate node makes for recommendation cards (§6) and `_enforce_grounding`
makes for chat prose, applied to the diagram: a model asked to draw a learning
path will happily invent an edge from a course we sell to a course we do not,
and a plausible-looking wrong diagram is harder to catch than a wrong sentence.

So the model chooses *which* courses to talk about, and this module draws the
ladder that the catalog says connects them.

Output is mermaid source. Mermaid because it renders client-side from a text
string — no image generation, no server-side layout, and a chat reply stays a
JSON payload rather than becoming a binary attachment.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.db.models import Product
from app.db.session import async_session

# Mermaid node ids must be identifier-safe; labels must not contain quotes or
# newlines or the diagram fails to parse — silently, in the browser.
_SAFE_ID = re.compile(r"[^A-Za-z0-9]")


def _node_id(slug: str) -> str:
    return "c" + _SAFE_ID.sub("", slug)[:28]


def _label(text: str, width: int = 26) -> str:
    """Wrap a title into a mermaid label with <br/> breaks.

    Long titles are the norm in this catalog ("Ultimate RAG Bootcamp: Building
    Traditional to Agentic Systems with Cloud Deployment"), and unwrapped they
    render as one box wider than the viewport.
    """
    clean = re.sub(r'["`\n\r]', "", text or "").strip()
    words, lines, current = clean.split(), [], ""
    for index, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if len(lines) == 3:
            # Out of room with `word` (and everything after it) unplaced.
            # Say so: a silently truncated label reads as the course's real
            # name, which is wrong on a diagram people screenshot.
            lines[-1] = lines[-1].rstrip(" ,:;-") + "…"
            return "<br/>".join(lines)
        current = word
        del index
    if current and len(lines) < 3:
        lines.append(current)
    return "<br/>".join(lines) or clean[:width]


def _price(p: Product) -> str:
    return "Free" if not p.price else f"₹{int(p.price):,}"


# A capability is a phrase; a product is a name. "Multi-agent orchestration
# and handoffs" tells you what you will be able to do, "FAISS" tells you what
# will be installed. Structure separates them better than a blocklist does —
# a blocklist has to be extended for every vendor the catalog adds, and the
# one that was here missed FAISS, Tavily, ChromaDB and Gemini on first run.
#
# Two signals, both structural:
#   * a capability is multi-word (products are usually one token)
#   * a capability is lowercase past its first word ("Agent memory systems"),
#     where a product name keeps capitals throughout ("AWS Lambda", "Phi Data")
_GENERIC_HEAD = re.compile(
    r"^(advanced|agentic|multi|cross|end)\b[- ]", re.I)


def _is_capability(tag: str) -> bool:
    words = tag.split()
    if len(words) < 2:
        return False                     # "FAISS", "LangGraph", "Docker"
    # Ignore a leading qualifier when judging capitalisation, so "Advanced RAG
    # (hybrid search…)" is judged on "RAG (hybrid search…)".
    tail = words[1:] if _GENERIC_HEAD.match(tag) else words
    lowercase_tail = sum(1 for w in tail[1:] if w[:1].islower())
    return lowercase_tail >= max(1, (len(tail) - 1) // 2)


def _capabilities(p: Product, limit: int = 3) -> list[str]:
    """The skills worth naming on a path step, capabilities before tools.

    `tags` on this catalog are long — 40 entries is normal — and dumping them
    is as unreadable as showing none. The split matters more than the cut: a
    reader deciding whether a step is for them is asking "what will I be able
    to do", and a list led by product names answers a different question.
    """
    tags = [t.strip() for t in (p.tags or []) if isinstance(t, str) and t.strip()]
    if not tags:
        return []
    capabilities = [t for t in tags if _is_capability(t)]
    tools = [t for t in tags if not _is_capability(t)]
    # Shortest first, but only among things that already read as capabilities —
    # sorting the whole list by length is what promoted "FAISS" over
    # "Multi-agent orchestration and handoffs".
    capabilities.sort(key=len)
    picked = capabilities[:limit]
    if len(picked) < limit:
        picked += [t for t in tools if t not in picked][:limit - len(picked)]
    return picked


def _start_label(start_from: str = "") -> str:
    """The left terminal: where this person stands today.

    Named from their current role when we know it, because "Data Analyst →
    … → shipping agents in production" is a story and "You are here → …" is a
    placeholder. Falls back to the neutral phrasing rather than inventing a
    role we were never told.
    """
    role = (start_from or "").strip()
    return f"Today: {role}" if role else "Where you are today"


def _goal_label(goal: str = "") -> str:
    """The right terminal: what they can DO, not the title they started with.

    Repeating the goal verbatim on both ends is what made the diagram read as
    a loop. Prefixing the destination makes the arrow mean something even when
    the goal is the only string we have.
    """
    goal = (goal or "").strip()
    return f"Ready for: {goal}" if goal else "Job ready"


def _final_gain(ordered: list[Product]) -> str:
    """The label on the arrow into the goal.

    Deliberately NOT another skill list. The last course's skills are already
    on the edge leading into it, so repeating them here printed the same
    caption twice in a row. This edge answers a different question — what the
    whole path leaves you holding — so it speaks about the set, not the last
    item.
    """
    if not ordered:
        return ""
    projects = sum(1 for p in ordered if "project" in (p.title or "").lower()
                   or "bootcamp" in (p.title or "").lower())
    if projects:
        return f"{projects} builds for your portfolio"
    return f"{len(ordered)} courses done"


def _edge_label(text: str, limit: int = 38) -> str:
    """Trim a gain down to something that fits on an arrow.

    Edge labels are not wrapped by mermaid the way node labels are, so a long
    one stretches the whole column it sits in and the diagram stops fitting.
    Quotes and pipes are stripped because both terminate the label syntax.
    """
    clean = re.sub(r'["`|\n\r]', "", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rsplit(" ", 1)[0].rstrip(" ,") + "…"


def _gain(p: Product, previous: Product | None) -> str:
    """One line on what THIS step adds that the one before it did not.

    The delta, not the contents. A path whose every step is described in
    isolation reads as three unrelated courses that happen to be adjacent; the
    thing that makes it a path is that each rung is reachable from the last.
    """
    # Draw from a wider pool than we show. De-duplicating a 3-item list against
    # the previous step can leave one survivor ("LCEL" alone, on real data);
    # taking 3 of 10 after filtering keeps the line full AND honest.
    skills = _capabilities(p, 10)
    if not skills:
        return ""
    if previous is not None:
        # Only claim what is genuinely new — a repeated skill described as a
        # gain is the kind of small dishonesty that costs trust on the second
        # read, when the user notices both boxes said "RAG".
        seen = {s.lower() for s in _capabilities(previous, 10)}
        fresh = [s for s in skills if s.lower() not in seen]
        skills = fresh or skills
    return ", ".join(skills[:3])


async def _resolve_prereqs(courses: list[Product]) -> dict[int, list[Product]]:
    """Fetch the prerequisite courses referenced by the given ones.

    `prereq_ids` holds slugs (the curated JSON is slug-keyed), so this is a
    lookup rather than a join. Only active courses come back — a path that
    routes through a retired course is not a path anyone can take.
    """
    wanted: set[str] = set()
    for p in courses:
        for slug in (p.prereq_ids or []) if isinstance(p.prereq_ids, list) else []:
            if isinstance(slug, str):
                wanted.add(slug)
    if not wanted:
        return {}

    async with async_session() as s:
        rows = list((await s.execute(
            select(Product).where(Product.slug.in_(wanted),
                                  Product.is_active.is_(True))
        )).scalars().all())
    by_slug = {p.slug: p for p in rows}

    out: dict[int, list[Product]] = {}
    for p in courses:
        prereqs = [by_slug[s] for s in (p.prereq_ids or [])
                   if isinstance(s, str) and s in by_slug]
        if prereqs:
            out[p.id] = prereqs
    return out


def _stage_order(courses: list[Product]) -> list[Product]:
    """Order a flat set of courses into something that reads as a path.

    By level first, then price. Level is the honest ordering signal — it is
    what the catalog states about difficulty — and price breaks ties toward
    starting cheap, which is the right default when nothing else distinguishes
    two courses at the same level.
    """
    rank = {"beginner": 0, "intermediate": 1, "advanced": 2}
    return sorted(courses, key=lambda p: (rank.get((p.level or "").lower(), 1),
                                          p.price or 0))


async def build_pathway_async(courses: list[Product], goal: str = "",
                              start_from: str = "") -> dict | None:
    """Build the flowchart. Returns None when there is nothing worth drawing.

    One course with no prerequisites is not a path — it is a suggestion, and
    the card already says it better than a one-box diagram would.
    """
    courses = [p for p in courses if p is not None][:4]
    if not courses:
        return None

    prereqs = await _resolve_prereqs(courses)
    ordered = _stage_order(courses)
    if len(ordered) < 2 and not prereqs:
        return None

    lines = ["flowchart LR"]
    seen: set[int] = set()
    edges: list[str] = []

    # The old diagram put `goal` in BOTH terminals, so it read
    # "Senior AI Architect → … → Senior AI Architect" — a loop saying the user
    # ends where they started, which is the opposite of the point. The two
    # ends must name a *transition*: where they stand now, and what they can
    # do once the path is walked.
    lines.append(f'  start(["{_label(_start_label(start_from), 24)}"])')

    previous = "start"
    previous_product: Product | None = None
    for index, p in enumerate(ordered, start=1):
        node = _node_id(p.slug)
        if p.id not in seen:
            seen.add(p.id)
            lines.append(f'  {node}["<b>{index}. {_label(p.title)}</b><br/>'
                         f'<small>{p.level} · {_price(p)}</small>"]')

        # A prerequisite the catalog states is drawn before the course, so the
        # diagram shows the real entry point rather than implying the user can
        # start anywhere.
        chain_head = node
        for pre in prereqs.get(p.id, [])[:1]:
            pre_node = _node_id(pre.slug)
            if pre.id not in seen:
                seen.add(pre.id)
                lines.append(f'  {pre_node}["{_label(pre.title)}<br/><small>'
                             f'{pre.level} · {_price(pre)}</small>"]')
            edges.append(f"  {pre_node} -->|prerequisite| {node}")
            chain_head = pre_node

        # The edge carries the gain, so the arrows read as "and then you can
        # do X" rather than as bare adjacency. This is the whole difference
        # between a flowchart and a numbered list drawn sideways.
        gain = _gain(p, previous_product)
        label = f'|"{_edge_label(gain)}"|' if gain else ""
        edges.append(f"  {previous} -->{label} {chain_head}")
        previous = node
        previous_product = p

    lines.append(f'  goal(["{_label(_goal_label(goal), 24)}"])')
    edges.append(f"  {previous} -->|\"{_edge_label(_final_gain(ordered))}\"| goal")

    lines.extend(edges)
    lines.append("  classDef step fill:#eef3fd,stroke:#2b5cd9,color:#12244a;")
    if seen:
        lines.append("  class " + ",".join(
            _node_id(p.slug) for p in ordered if p.id in seen) + " step;")

    return {
        "mermaid": "\n".join(lines),
        "steps": _steps(ordered),
        "goal": goal or "",
    }


def _steps(ordered: list[Product]) -> list[dict]:
    """The text form of the path. Carries the same gains as the diagram.

    Built here rather than inline in both builders so the list under the
    diagram can never describe a different path from the diagram itself.
    """
    out, previous = [], None
    for p in ordered:
        out.append({"id": p.id, "slug": p.slug, "title": p.title,
                    "level": p.level, "price": p.price,
                    "gain": _gain(p, previous)})
        previous = p
    return out


def build_pathway(courses: list[Product], goal: str = "",
                  start_from: str = "") -> dict | None:
    """Sync wrapper used by the answer payload.

    The prerequisite lookup needs a DB round trip, so this schedules the async
    builder on the running loop. Kept as a separate function so callers that
    are already async can await `build_pathway_async` directly.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(build_pathway_async(courses, goal, start_from))

    # Already inside the event loop (the normal case: a request handler).
    # A task cannot be awaited synchronously here, so the caller is expected to
    # use the async form; this path exists for safety and returns the
    # no-prereq diagram rather than blocking the loop.
    del loop
    return _build_without_prereqs(courses, goal, start_from)


def _build_without_prereqs(courses: list[Product], goal: str = "",
                           start_from: str = "") -> dict | None:
    """Diagram from the given courses alone, no DB access."""
    courses = [p for p in courses if p is not None][:4]
    if len(courses) < 2:
        return None
    ordered = _stage_order(courses)
    lines = ["flowchart LR",
             f'  start(["{_label(_start_label(start_from), 24)}"])']
    edges, previous, previous_product = [], "start", None
    for index, p in enumerate(ordered, start=1):
        node = _node_id(p.slug)
        lines.append(f'  {node}["<b>{index}. {_label(p.title)}</b><br/>'
                     f'<small>{p.level} · {_price(p)}</small>"]')
        gain = _gain(p, previous_product)
        label = f'|"{_edge_label(gain)}"|' if gain else ""
        edges.append(f"  {previous} -->{label} {node}")
        previous, previous_product = node, p
    lines.append(f'  goal(["{_label(_goal_label(goal), 24)}"])')
    edges.append(f"  {previous} -->|\"{_edge_label(_final_gain(ordered))}\"| goal")
    lines.extend(edges)
    lines.append("  classDef step fill:#eef3fd,stroke:#2b5cd9,color:#12244a;")
    lines.append("  class " + ",".join(_node_id(p.slug) for p in ordered) + " step;")
    return {"mermaid": "\n".join(lines), "steps": _steps(ordered),
            "goal": goal or ""}
