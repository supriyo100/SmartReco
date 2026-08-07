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

# Turns of history sent back to the model. Enough to hold a thread ("what about
# the cheaper one?"), bounded so a long conversation cannot grow the prompt
# without limit — the cost of turn N must not depend on N.
HISTORY_TURNS = 8
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
something outside learning and careers, answer briefly and steer back."""


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

    facts["has_profile"] = bool(lines)
    return ("\n".join(f"- {line}" for line in lines)
            or "- Nothing known about this user yet (new account, no resume, "
               "no browsing history). Ask one short question to orient.",
            facts)


async def _history(conversation_id: int) -> list[dict]:
    """Last N turns, oldest first."""
    async with async_session() as s:
        rows = (await s.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(HISTORY_TURNS * 2)
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

    # Retrieval query: the message plus the target role, so "what should I
    # learn next?" — which contains no retrievable nouns at all — still pulls
    # courses relevant to where they are going.
    query = message
    role_hint = ""
    for line in context.split("\n"):
        if "TARGET ROLE:" in line:
            role_hint = line.split("TARGET ROLE:", 1)[1].strip()
    if role_hint:
        query = f"{message} {role_hint}"

    courses, path = await retrieve(query, top_k=RETRIEVE_K,
                                   max_price=facts.get("budget_max"))
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

    if not settings.use_mesh:
        reply = _offline_reply(message, courses, context)
        reply, cited = _enforce_grounding(reply, allowed)
        return {"answer": reply, "cited": cited, "model": "offline",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "retrieval_path": path, "courses": courses}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system",
         "content": f"WHAT YOU KNOW ABOUT THIS USER:\n{context}\n\n"
                    f"CATALOG (the ONLY courses you may name):\n{catalog_block}"},
        *history,
        {"role": "user", "content": message},
    ]

    try:
        from app.agent.mesh import _chat
        resp = await _chat(settings.MODEL_WRITER, messages, temperature=0.6,
                           max_tokens=700)
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

    return {"answer": reply, "cited": cited, "model": model_used,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "retrieval_path": path, "courses": courses}


async def get_or_create_conversation(user_id: int,
                                     conversation_id: int | None) -> Conversation:
    """Resolve a conversation, verifying ownership.

    The id arrives from the client, so it is checked against `user_id` — an
    unchecked id would let anyone read anyone else's thread by guessing a
    number. Falls back to the user's most recent thread, then to a new one.
    """
    async with async_session() as s:
        if conversation_id:
            conv = (await s.execute(
                select(Conversation).where(Conversation.id == conversation_id,
                                           Conversation.user_id == user_id)
            )).scalar_one_or_none()
            if conv is not None:
                return conv
        conv = Conversation(user_id=user_id, title="")
        s.add(conv)
        await s.commit()
        await s.refresh(conv)
        return conv
