"""The career-advice chat agent.

The architecture doc listed conversation memory under "deliberately not built",
on the grounds that "there is no conversation in this product — users browse,
they never chat with the agent." That is no longer true of the product, so the
line is superseded rather than quietly ignored: users now do chat, and an
advisor that forgets the previous turn is not an advisor.

What it knows, in priority order:

  1. The user's ATS gaps — the most specific, most actionable thing we have.
     "You're missing LangGraph for the Agentic AI Engineer roles you're
     targeting" is advice; "consider upskilling" is filler.
  2. Their declared profile — target role, goals, budget, hours per week.
  3. Their behavioral interest vector, when the interest model has one.
  4. Retrieved courses from the real catalog.

The grounding guarantee is the same one the recommendation cards carry (§0):
the prompt names the only courses that may be mentioned, and the response is
checked against that set afterwards. A model that names a course we do not
sell is a support ticket, and at worst a lie with our brand on it.

Cost control follows §5.1's logic, adapted: a chat turn is user-initiated, so
the trigger question is not "should we run" but "how little context can we
send". History is capped, retrieval is capped, and the whole prompt is built
from rows already in memory rather than from extra round trips.
"""
from __future__ import annotations

import logging
import re
import time

from sqlalchemy import select

from app.agent.providers import any_chat_provider
from app.chat.context import compact, extract_facts, retrieval_query, split_horizons
from app.chat.retrieval import fallback_catalog, retrieve
from app.config import settings
from app.db.models import (
    ChatMessage,
    Conversation,
    Product,
    Recommendation,
    ResumeAnalysis,
    UserProfile,
)
from app.db.session import async_session

log = logging.getLogger("chat.agent")

# How far back the DB read goes. Larger than the verbatim window because the
# older half is still mined for durable facts (budget, target role, topics)
# before being dropped — see app/chat/context.py.
HISTORY_READ = 60
MAX_MESSAGE_CHARS = 2000
RETRIEVE_K = 6

SYSTEM_PROMPT = """You are the career advisor for SmartReco, a course platform.

You help people decide what to learn next for the career they actually want. \
You are direct, specific and warm — a good mentor, not a brochure.

RULES, in order of importance:

1. GROUNDING. You may only mention courses that appear in CATALOG below. Never \
invent a course, a price, a date or an instructor. If nothing in CATALOG fits \
what they asked, say so plainly and give the career advice anyway — honest \
"we don't have that yet" beats a bad match.
2. Refer to a course by its exact title, and put [[id:N]] immediately after it \
using its id from CATALOG. The interface turns that into a card. Never show \
the [[id:N]] marker in a sentence you would want read aloud — it is a tag, so \
keep it tight against the title.
3. Use their profile. If you know their target role, their resume gaps or \
their budget, the answer should be visibly different from generic advice. \
Name a specific gap when you have one.
4. At most 3 courses in a reply. Recommending five things is how people decide \
to do none of them.
5. Be concise: 2-4 short paragraphs, or a short list. No preamble, no \
"great question", no restating what they asked.
6. Money and time are real constraints. Respect a stated budget and a stated \
weekly-hours limit rather than talking around them.
7. You are not the only source of truth about their career. If they ask \
something outside learning and careers, answer briefly and steer back.
8. EXPLAIN THE PICK. When a course appears under ALREADY RECOMMENDED you are \
told why the ranker chose it — a resume gap it closes, a category they read, a \
prerequisite they already viewed. If they ask "why this one?", answer with \
that real reason, not a guess. Never claim a reason you were not given.
9. Say what it CHANGES for them, not what it contains. "Adds the LangGraph \
and agent-orchestration your resume is missing for Agentic AI Engineer roles" \
beats "covers 17 modules on agents". Tie it to the role they are targeting, \
the gap they have, or the money and time they said they had."""


def _fmt_course(p: Product) -> str:
    bits = [f"id:{p.id} | {p.title}",
            f"category: {p.category}", f"level: {p.level}"]
    if p.price is not None:
        bits.append("price: free" if not p.price else f"price: ₹{int(p.price):,}")
    if p.rating:
        bits.append(f"rating: {p.rating}")
    if p.instructor:
        bits.append(f"instructor: {p.instructor}")
    desc = (p.description or "").strip().replace("\n", " ")
    if desc:
        bits.append(f"about: {desc[:280]}")
    return " | ".join(bits)


async def build_user_context(user_id: int) -> tuple[str, dict]:
    """Assemble what we know about this person into prompt text.

    One session, three reads, no LLM call. Returns the text plus the raw facts,
    because the caller uses `budget_max` as a retrieval filter and not only as
    prompt content.
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
        rec = (await s.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user_id,
                   Recommendation.is_current.is_(True))
            .order_by(Recommendation.created_at.desc())
        )).scalars().first()

    lines: list[str] = []
    facts: dict = {"budget_max": None}

    if profile:
        if profile.full_name:
            lines.append(f"Name: {profile.full_name}")
        if profile.headline:
            lines.append(f"Headline: {profile.headline}")
        if profile.current_role:
            lines.append(f"Current role: {profile.current_role}")
        if profile.target_role:
            lines.append(f"TARGET ROLE: {profile.target_role}")
        if profile.experience_years is not None:
            lines.append(f"Experience: {profile.experience_years} years")
        if profile.goals:
            lines.append(f"Stated goal: {profile.goals[:400]}")
        if profile.skills:
            lines.append(f"Skills they claim: {', '.join(list(profile.skills)[:25])}")
        if profile.budget_max is not None:
            lines.append(f"BUDGET: at most ₹{int(profile.budget_max):,}")
            facts["budget_max"] = profile.budget_max
        if profile.weekly_hours:
            lines.append(f"Time available: ~{profile.weekly_hours} h/week")
        if profile.preferred_mode:
            lines.append(f"Prefers: {profile.preferred_mode} courses")
        # The interest vector is behavioral truth and outranks claimed skills
        # when they disagree — what someone reads all week is a better signal
        # of intent than a list they wrote once.
        if profile.interests:
            top = sorted(profile.interests.items(), key=lambda kv: -float(kv[1] or 0))[:5]
            if top:
                lines.append("Browsing shows interest in: " +
                             ", ".join(f"{k}" for k, _ in top))

    if ats:
        lines.append(f"RESUME ATS SCORE: {ats.ats_score}/100 against "
                     f"'{ats.target_role}'")
        if ats.missing_skills:
            lines.append("RESUME GAPS (missing for that role): " +
                         ", ".join(list(ats.missing_skills)[:10]))
        if ats.matched_skills:
            lines.append("Already evidenced on resume: " +
                         ", ".join(list(ats.matched_skills)[:12]))
    elif profile and not (profile.resume_text or ""):
        lines.append("No resume uploaded yet — suggest it once if relevant, "
                     "then drop it.")

    if rec and rec.narrative:
        lines.append(f"Their current recommendation summary: {rec.narrative[:300]}")
    if rec and rec.items:
        # The stored per-card reasoning, so the advisor can EXPLAIN a pick
        # rather than re-deriving one. `terms` are the actual scoring inputs
        # from fusion_rank, which is what lets the chat answer "why is this
        # first?" with the real reason instead of a plausible-sounding one.
        for item in sorted(rec.items, key=lambda i: i.get("rank", 0))[:4]:
            terms = item.get("terms") or {}
            why = []
            if terms.get("gap_match", 0) > 0:
                why.append("closes resume gaps")
            if terms.get("interest_match", 0) > 0.15:
                why.append("matches what they browse")
            if terms.get("graph_adjacency", 0) >= 1.0:
                why.append("next step after a course they viewed")
            if terms.get("level_fit", 0) >= 1.0:
                why.append("right level for their experience")
            lines.append(
                f"ALREADY RECOMMENDED id:{item.get('product_id')} "
                f"(rank {item.get('rank')}, confidence "
                f"{item.get('confidence')}): {item.get('hook', '')} "
                + (f"[scored because: {', '.join(why)}]" if why else ""))

    facts["has_profile"] = bool(lines)
    return ("\n".join(f"- {line}" for line in lines)
            or "- Nothing known about this user yet (new account, no resume, "
               "no browsing history). Ask one short question to orient.",
            facts)


async def _history(conversation_id: int) -> list[dict]:
    """Conversation turns, oldest first.

    Reads more than goes into the prompt. The recent window is sent verbatim
    and everything older is compacted into durable facts (app/chat/context.py),
    so the read has to cover both — a LIMIT sized to the verbatim window would
    silently throw away the budget the user stated in turn three.
    """
    async with async_session() as s:
        rows = (await s.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(HISTORY_READ)
        )).scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


CITE_RE = re.compile(r"\[\[id:(\d+)\]\]")
# A cited course is normally written as **Title** [[id:N]]. When the id is
# ungrounded the TITLE has to go with it — removing only the marker leaves an
# invented course name sitting in the prose as plain text, which is the exact
# claim the grounding rule exists to prevent, just without a link on it.
CITED_TITLE_RE = re.compile(r"\*\*([^*]{1,120})\*\*[\s—–-]*\[\[id:(\d+)\]\]")


def _enforce_grounding(answer: str, allowed: set[int]) -> tuple[str, list[int]]:
    """Strip citations — and invented course titles — that retrieval did not back.

    This is the chat equivalent of the validate node (§6), and it exists for
    the same reason: a rule stated in a prompt is a request, while a rule
    enforced in code is a guarantee. A hallucinated id is removed rather than
    rendered as a card that 404s.
    """
    cited: list[int] = []

    def drop_invented(match: re.Match) -> str:
        pid = int(match.group(2))
        if pid in allowed:
            return match.group(0)          # keep; the pass below records it
        log.warning("chat: dropped ungrounded course %r (id=%s)",
                    match.group(1), pid)
        return ""

    # Title-with-citation first, so a fabricated course loses its name too.
    text = CITED_TITLE_RE.sub(drop_invented, answer)

    def keep(match: re.Match) -> str:
        pid = int(match.group(1))
        if pid in allowed:
            if pid not in cited:
                cited.append(pid)
            return match.group(0)
        log.warning("chat: dropped ungrounded citation id=%s", pid)
        return ""

    # Then any bare markers left over (a citation with no bolded title).
    text = CITE_RE.sub(keep, text)
    # Removing a list item can leave a dangling connector or empty bullet.
    text = re.sub(r"(?m)^\s*[-*]\s*$", "", text)
    text = re.sub(r",\s*(?:or|and)\s*\.", ".", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), cited


def _offline_reply(message: str, courses: list[Product], context: str) -> str:
    """Deterministic answer for when Mesh is unavailable.

    The test suite runs offline by design (`settings.use_mesh` is false with no
    key), and a chat panel that 500s without a key is untestable and undemoable.
    This is visibly a fallback — it never pretends to be the model.
    """
    if not courses:
        return ("I can't reach the recommendation model right now, and nothing "
                "in the catalog matched that closely. Try naming a specific "
                "skill — 'RAG', 'LangGraph', 'MLOps' — and I'll point you at "
                "the closest course we have.")
    lines = ["I'm running without the language model right now, so here is a "
             "direct catalog match rather than tailored advice:"]
    for p in courses[:3]:
        price = "free" if not p.price else f"₹{int(p.price):,}"
        lines.append(f"- **{p.title}** [[id:{p.id}]] — {p.category}, "
                     f"{p.level}, {price}")
    return "\n".join(lines)


async def answer(user_id: int, conversation_id: int, message: str) -> dict:
    """Produce one grounded reply. Returns the payload the route persists."""
    started = time.perf_counter()
    message = (message or "").strip()[:MAX_MESSAGE_CHARS]

    context, facts = await build_user_context(user_id)
    history = await _history(conversation_id)

    # Two horizons. `recent` goes to the model verbatim so follow-ups resolve;
    # everything older is compacted to durable facts so the prompt stays flat
    # as the thread grows (§5.1: the cost of turn N must not depend on N).
    older, recent = split_horizons(history)
    compacted = compact(older)

    # Facts stated in conversation override the stored profile. Someone who
    # says "actually my budget is 5000" in turn four means it now, and a
    # profile field they filled in weeks ago should not silently win.
    said = extract_facts(history + [{"role": "user", "content": message}])
    role_hint = said.get("target_role") or ""
    if not role_hint:
        for line in context.split("\n"):
            if "TARGET ROLE:" in line:
                role_hint = line.split("TARGET ROLE:", 1)[1].strip()
    budget = said.get("budget_max") or facts.get("budget_max")

    # Retrieval searches a widened string; the reranker scores against what
    # they actually asked. See retrieval_query() and retrieve() for why.
    query = retrieval_query(message, recent, role_hint, said.get("topics"))
    profile_terms = set(said.get("topics") or [])
    if role_hint:
        profile_terms |= {t for t in role_hint.lower().split() if len(t) > 3}

    courses, path = await retrieve(
        query, top_k=RETRIEVE_K, max_price=budget,
        rerank_query=message or query, profile_terms=profile_terms,
        level=_level_hint(context),
    )
    used_fallback = False
    if not courses:
        courses = await fallback_catalog(top_k=4)
        used_fallback = True

    catalog_block = "\n".join(_fmt_course(p) for p in courses) or "(empty catalog)"
    if used_fallback and courses:
        catalog_block = ("(No close match for this question — these are general "
                         "catalog entries. Do not claim they match precisely.)\n"
                         + catalog_block)

    allowed = {p.id for p in courses}

    if not any_chat_provider():
        reply = _offline_reply(message, courses, context)
        reply, cited = _enforce_grounding(reply, allowed)
        return await _payload(reply, cited, "offline", started, path, courses,
                              message, role_hint, said, history)

    knowledge = f"WHAT YOU KNOW ABOUT THIS USER:\n{context}"
    if compacted:
        knowledge += f"\n\n{compacted}"
    knowledge += f"\n\nCATALOG (the ONLY courses you may name):\n{catalog_block}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": knowledge},
        *recent,
        {"role": "user", "content": message},
    ]

    try:
        from app.agent.mesh import _chat
        resp = await _chat(settings.MODEL_WRITER, messages, temperature=0.6,
                           max_tokens=900)
        raw = (resp.choices[0].message.content or "").strip()
        model_used = settings.MODEL_WRITER
    except Exception as exc:
        log.warning("chat model call failed (%s); serving offline reply", exc)
        raw = _offline_reply(message, courses, context)
        model_used = "offline-error"

    reply, cited = _enforce_grounding(raw, allowed)
    if not reply:
        reply = ("I couldn't put together a useful answer for that. Try asking "
                 "about a specific skill or role.")

    return await _payload(reply, cited, model_used, started, path, courses,
                          message, role_hint, said, history)


def _level_hint(context: str) -> str | None:
    """Map stated experience to a catalog level, for the reranker's mild nudge."""
    match = re.search(r"Experience: (\d+) years", context)
    if not match:
        return None
    years = int(match.group(1))
    return "beginner" if years < 2 else "intermediate" if years < 6 else "advanced"


async def _payload(reply: str, cited: list[int], model: str, started: float,
                   path: str, courses: list[Product], message: str,
                   role_hint: str, said: dict, history: list[dict]) -> dict:
    """Assemble the response: prose, flowchart, and buy-intent signal.

    The flowchart is built from the courses the answer actually cited — not
    from what the model drew — so it carries the same grounding guarantee as
    the prose. See app/chat/pathway.py.
    """
    from app.chat.intent import score_intent
    from app.chat.pathway import build_pathway_async

    by_id = {p.id: p for p in courses}
    cited_courses = [by_id[pid] for pid in cited if pid in by_id]
    # Both ends of the diagram, not just the destination. Drawing the goal on
    # the left as well made the path read as a loop back to where it started;
    # naming today's role turns it into a before-and-after.
    pathway = await build_pathway_async(cited_courses or courses[:3],
                                        goal=role_hint,
                                        start_from=said.get("current_role") or "")

    intent = score_intent(message, history, cited_courses or courses[:1], said)

    return {"answer": reply, "cited": cited, "model": model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "retrieval_path": path, "courses": courses,
            "pathway": pathway, "intent": intent,
            # Facts mined from the thread, persisted on the conversation so the
            # next turn does not re-derive them and lead-reading tools can see
            # what the user actually said they wanted.
            "facts": {k: v for k, v in (said or {}).items() if v}}


async def get_or_create_conversation(user_id: int,
                                     conversation_id: int | None,
                                     session_id: str = "",
                                     tenant_id: int = 1) -> Conversation:
    """Resolve a conversation, verifying ownership.

    The id arrives from the client, so it is checked against `user_id` — an
    unchecked id would let anyone read anyone else's thread by guessing a
    number. Falls back to the user's most recent thread, then to a new one.

    A new thread captures a snapshot of what was known about the person at the
    time. Advice can only be judged against the facts it was given, and those
    facts change — a reply that reads as wrong today may have been right for a
    profile that has since been edited.
    """
    async with async_session() as s:
        if conversation_id:
            conv = (await s.execute(
                select(Conversation).where(Conversation.id == conversation_id,
                                           Conversation.user_id == user_id)
            )).scalar_one_or_none()
            if conv is not None:
                # A returning browser gets its session stamped on the existing
                # thread: threads outlive sessions, and the latest one is what
                # correlates with current browsing.
                if session_id and conv.session_id != session_id:
                    conv.session_id = session_id
                    await s.commit()
                return conv

        snapshot = await _user_snapshot(s, user_id)
        conv = Conversation(user_id=user_id, title="", session_id=session_id,
                            tenant_id=tenant_id, user_snapshot=snapshot)
        s.add(conv)
        await s.commit()
        await s.refresh(conv)
        return conv


async def _user_snapshot(session, user_id: int) -> dict:
    """Small, stable record of who this person was when the thread opened."""
    profile = (await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )).scalar_one_or_none()
    ats = (await session.execute(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user_id, ResumeAnalysis.is_current.is_(True))
        .order_by(ResumeAnalysis.created_at.desc())
    )).scalars().first()

    return {
        "target_role": getattr(profile, "target_role", "") or "",
        "current_role": getattr(profile, "current_role", "") or "",
        "experience_years": getattr(profile, "experience_years", None),
        "budget_max": getattr(profile, "budget_max", None),
        "weekly_hours": getattr(profile, "weekly_hours", None),
        "skills": list(getattr(profile, "skills", []) or [])[:20],
        "ats_score": getattr(ats, "ats_score", None),
        "missing_skills": list(getattr(ats, "missing_skills", []) or [])[:10],
    }
