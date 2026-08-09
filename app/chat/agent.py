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

import asyncio
import logging
import re
import time
import uuid

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

# LangGraph counts each model-node/tool-node hop as a step, and the 8-layer
# middleware stack (PII, call limits, retry, HITL, fallback, grounding) adds
# its own hops on top of that. The real cost caps are
# CHAT_MODEL_CALL_LIMIT_PER_TURN and CHAT_TOOL_CALL_LIMIT_PER_TURN
# (settings.py, tightened to 3/4) — this is a hard safety net above them, not
# the primary limiter, and must stay clear of it: at the old fixed 25, a
# normal multi-tool-call turn's ~20 steps left almost no margin, so the
# model's more exploratory runs crashed to the offline fallback mid-turn —
# wasting a completed model call — instead of the graceful "end" the call-
# limit middleware is supposed to produce. Deriving it from the same knobs
# means tightening the caps for cost also tightens this net, in step.
RECURSION_LIMIT = 3 * (settings.CHAT_MODEL_CALL_LIMIT_PER_TURN
                       + settings.CHAT_TOOL_CALL_LIMIT_PER_TURN) + 10

SYSTEM_PROMPT = """You are the career advisor for SmartReco, a course platform.

You help people decide what to learn next for the career they actually want. \
You are direct, specific and warm — a good mentor, not a brochure.

RULES, in order of importance:

1. GROUNDING. You may only mention a course you actually retrieved this turn \
— from the FIRST-PASS SEARCH below, or from calling search_catalog / \
get_course_details yourself. Never invent a course, a price, a date or an \
instructor. If nothing fits what they asked, say so plainly and give the \
career advice anyway — honest "we don't have that yet" beats a bad match.
2. Refer to a course by its exact title, and put [[id:N]] immediately after it \
using its id. The interface turns that into a card with a link to the course \
and its syllabus. Never show the [[id:N]] marker in a sentence you would want \
read aloud — it is a tag, so keep it tight against the title. This applies to \
EVERY course you discuss, including a second or third one in the same reply — \
never write a reason, a "Why:" line, or a bullet about a course without first \
naming it this way. A reason with no named, cited course in front of it is \
useless to the reader: they cannot tell what it is a reason FOR. Formatting: \
**bold** only — never wrap a word in single asterisks, that renders as a \
literal asterisk in the interface, not emphasis.
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
prerequisite they already viewed. If they ask "why this one?", or ask about a \
topic that several ALREADY RECOMMENDED courses cover, answer with that real \
reason, not a guess — but rule 2 still applies to each one: name it first. \
Right: "**Ultimate RAG Bootcamp** [[id:13]] — closes your vector-database gap." \
Wrong: "Why: closes your vector-database gap." (no course named — the reader \
has nothing to click and no idea which course you mean). Never claim a reason \
you were not given.
9. Say what it CHANGES for them, not what it contains. "Adds the LangGraph \
and agent-orchestration your resume is missing for Agentic AI Engineer roles" \
beats "covers 17 modules on agents". Tie it to the role they are targeting, \
the gap they have, or the money and time they said they had.
10. TOOLS. Use search_catalog when the FIRST-PASS SEARCH does not fit what \
they asked, and get_course_details when they ask what a course actually \
covers before you recommend it. If they state a durable new fact — a \
different target role, a budget, weekly hours — use update_learner_profile; \
that pauses for their confirmation, so tell them what you are about to save \
in the same reply. If a new fact changes what they should be learning, use \
refresh_recommendations afterward.
11. IF A SUGGESTION DOESN'T FIT. When they say a course is wrong — wrong \
depth, missing a topic, wrong format — do not immediately search again. Ask \
one short, specific question about what they actually need (a skill, a \
level, a format, a timeline), then search once with the answer. This applies \
even if search_catalog would technically let you try again; guessing twice \
in a row reads as not listening the first time."""


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
        bits.append(f"about: {desc[:settings.PROMPT_DESCRIPTION_CHARS]}")
    return " | ".join(bits)


async def build_user_context(user_id: int) -> tuple[str, dict]:
    """What we know about this person, as prompt text.

    Reads the brief `app/chat/brief.py` renders and stores at write-time
    (profile save, ATS run, recommendation refresh) rather than re-deriving
    it from three tables on every chat turn — plan.md §1-5. A stale or
    never-populated brief (existing users, first deploy) is a cache miss,
    not an error: it falls back to rendering fresh and stores the result so
    the next turn is a hit.

    Returns the text plus the raw facts, because the caller uses
    `budget_max` and `weekly_hours` as retrieval/prompt inputs and not only
    as prompt content.
    """
    from app.chat.brief import _fingerprint, render_background_brief

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

        fp = _fingerprint(profile, ats, rec)
        if profile is not None and profile.background_brief \
                and profile.brief_fingerprint == fp:
            text = profile.background_brief
        else:
            text = render_background_brief(profile, ats, rec)
            if profile is not None:
                profile.background_brief = text
                profile.brief_fingerprint = fp
                await s.commit()

    facts: dict = {
        "budget_max": profile.budget_max if profile else None,
        "weekly_hours": profile.weekly_hours if profile else None,
        "has_profile": bool(profile and (profile.full_name or profile.target_role
                                         or profile.goals or profile.skills)) or bool(ats) or bool(rec),
    }
    return text, facts


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


_agent = None
_agent_lock: asyncio.Lock | None = None
_checkpointer_cm = None   # kept alive for the process's life — see _build_agent


async def _get_agent():
    """Build the tool-calling chat agent once, lazily.

    Reached only from the branch of `answer()` guarded by
    `any_chat_provider()` — the offline path returns before this is ever
    called, so the LangChain import graph and the checkpointer's sqlite file
    stay untouched by the (offline-by-design) test suite, same as `chain()`
    never touching the network under `ENV=test` today.
    """
    global _agent, _agent_lock
    if _agent is not None:
        return _agent
    if _agent_lock is None:
        _agent_lock = asyncio.Lock()
    async with _agent_lock:
        if _agent is None:
            _agent = await _build_agent()
    return _agent


async def _build_agent():
    import pathlib

    from langchain.agents import create_agent
    from langchain.agents.middleware import (
        HumanInTheLoopMiddleware,
        ModelCallLimitMiddleware,
        PIIMiddleware,
        ToolCallLimitMiddleware,
        ToolRetryMiddleware,
    )
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from app.agent.langchain_bridge import build_chat_models, mesh_fallback_middleware
    from app.agent.pii import ADDRESS_PATTERN, PHONE_PATTERN
    from app.chat.agent_middleware import chat_system_prompt, enforce_grounding, offline_reply
    from app.chat.tools import CHAT_TOOLS, ChatContext

    global _checkpointer_cm
    pathlib.Path("data").mkdir(exist_ok=True)
    _checkpointer_cm = AsyncSqliteSaver.from_conn_string("data/chat_checkpoints.sqlite")
    checkpointer = await _checkpointer_cm.__aenter__()

    # `model=` is what create_agent needs a BaseChatModel instance for up
    # front; mesh_fallback_middleware does the actual per-call provider
    # selection through providers.chain(), so which one this is barely
    # matters — see langchain_bridge.py.
    primary_model = build_chat_models()["mesh"]

    middleware = [
        # PII: redact identity-leaking data out of the live user message
        # before it reaches Mesh/Groq/Ollama. Built-in types cover email and
        # card numbers; phone/address are this product's own detectors
        # (app/agent/pii.py) since resumes are the actual risk surface here.
        PIIMiddleware("email", strategy="redact", apply_to_input=True),
        PIIMiddleware("credit_card", strategy="redact", apply_to_input=True),
        PIIMiddleware("phone", detector=PHONE_PATTERN, strategy="redact",
                      apply_to_input=True),
        PIIMiddleware("address", detector=ADDRESS_PATTERN, strategy="redact",
                      apply_to_input=True),
        # Cost/loop guards. search_catalog gets its own tighter cap so a
        # rejected suggestion pushes the model toward asking a clarifying
        # question (see SYSTEM_PROMPT) instead of re-searching indefinitely.
        ModelCallLimitMiddleware(run_limit=settings.CHAT_MODEL_CALL_LIMIT_PER_TURN,
                                 exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=settings.CHAT_TOOL_CALL_LIMIT_PER_TURN),
        ToolCallLimitMiddleware(tool_name="search_catalog",
                                run_limit=settings.CHAT_SEARCH_CALL_LIMIT_PER_TURN),
        ToolRetryMiddleware(max_retries=2, backoff_factor=2.0),
        # Human-in-the-loop: only a profile write inferred from conversation
        # pauses for confirmation. Search and recommendation refresh do not —
        # decided with the user rather than assumed.
        HumanInTheLoopMiddleware(interrupt_on={"update_learner_profile": True}),
        # wrap_model_call stack — first listed is outermost. offline_reply
        # must wrap mesh_fallback_middleware to catch total provider
        # exhaustion; grounding must be innermost so it sees the actual
        # model response before anything else touches it.
        offline_reply,
        mesh_fallback_middleware,
        chat_system_prompt,
        enforce_grounding,
    ]

    return create_agent(
        model=primary_model,
        tools=CHAT_TOOLS,
        middleware=middleware,
        context_schema=ChatContext,
        checkpointer=checkpointer,
    )


async def _products_for_cards(cited_ids: set[int], seed: list[Product]) -> list[Product]:
    """Product rows for every id the agent's tool calls could have cited.

    `seed` (the pre-agent retrieval) covers the common case; ids the agent
    reached through its own `search_catalog`/`get_course_details` calls with
    a refined query need one extra fetch so `_payload()`'s card-building
    never drops a citation just because it wasn't in the first-pass search.
    """
    have = {p.id for p in seed}
    missing = cited_ids - have
    if not missing:
        return seed
    async with async_session() as s:
        extra = (await s.execute(
            select(Product).where(Product.id.in_(missing), Product.is_active.is_(True))
        )).scalars().all()
    return [*seed, *extra]


async def answer(user_id: int, conversation_id: int, message: str) -> dict:
    """Produce one grounded reply. Returns the payload the route persists."""
    started = time.perf_counter()
    message = (message or "").strip()[:MAX_MESSAGE_CHARS]

    from app.chat.guardrails import check_budget

    budget = await check_budget(user_id, conversation_id)
    if not budget.allowed:
        return {"answer": budget.reason, "cited": [], "model": "budget-exceeded",
               "latency_ms": int((time.perf_counter() - started) * 1000),
               "retrieval_path": "blocked", "courses": [], "pathway": None,
               "intent": {}, "facts": {}}

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

    # PII is scrubbed here, not left to PIIMiddleware — that middleware only
    # scans the live HumanMessage, and this knowledge block is injected as
    # system content (via the dynamic-prompt middleware) built from resume
    # and profile text, which is exactly the surface that needs it.
    from app.agent.pii import redact_pii

    knowledge = f"WHAT YOU KNOW ABOUT THIS USER:\n{redact_pii(context)}"
    if compacted:
        knowledge += f"\n\n{redact_pii(compacted)}"
    # plan.md §10: a course and a 15-minute primer are different answers to
    # the same question depending on how much time someone actually has.
    # Nothing else asks for it, so nudge for it once, on the opening turn —
    # not every turn, which would read as nagging.
    if not history and not facts.get("weekly_hours") and not said.get("weekly_hours"):
        knowledge += ("\n\nTIME COMMITMENT: not stated yet. Ask directly in this "
                     "reply — how many hours a week, or a target timeline — "
                     "before assuming they want a multi-week course over a "
                     "quick primer.")
    if courses:
        knowledge += (f"\n\nA FIRST-PASS SEARCH ALREADY FOUND (call search_catalog "
                     f"again with a narrower query if none of these fit):\n{catalog_block}")

    from app.chat.tools import ChatContext

    thread_id = f"chat-{conversation_id}-{uuid.uuid4().hex[:10]}"
    lc_messages = [*recent, {"role": "user", "content": message}]

    try:
        agent = await _get_agent()
        result = await agent.ainvoke(
            {"messages": lc_messages},
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT},
            context=ChatContext(user_id=user_id, knowledge=knowledge,
                               conversation_id=conversation_id),
        )
    except Exception as exc:
        log.warning("chat agent invocation failed (%s); serving offline reply", exc)
        raw = _offline_reply(message, courses, context)
        reply, cited = _enforce_grounding(raw, allowed)
        return await _payload(reply, cited, "offline-error", started, path, courses,
                              message, role_hint, said, history)

    return await _finish_turn(result, thread_id, started, path, courses,
                              message, role_hint, said, history)


async def _finish_turn(result: dict, thread_id: str, started: float, path: str,
                       courses: list[Product], message: str, role_hint: str,
                       said: dict, history: list[dict]) -> dict:
    """Turn a graph result (from `answer()` or `resume()`) into the payload
    the route persists — or, if the run paused again, another interrupt."""
    if result.get("__interrupt__"):
        # HumanInTheLoopMiddleware paused the run (a profile write it wants
        # to confirm first). The route persists `thread_id` + the request so
        # POST /api/chat/resume can continue this exact paused run.
        hitl = result["__interrupt__"][0].value
        return {"interrupt": hitl, "thread_id": thread_id,
               "latency_ms": int((time.perf_counter() - started) * 1000)}

    final = result["messages"][-1]
    raw = str(final.content or "")
    meta = getattr(final, "response_metadata", None) or {}
    model_used = meta.get("model_name") or meta.get("model") or "chat-agent"

    # Grounding was already enforced inside the graph (enforce_grounding
    # middleware, app/chat/agent_middleware.py) against every id the agent's
    # own tool calls actually retrieved — re-checking here against a
    # narrower pre-agent `allowed` set would wrongly strip a citation the
    # agent legitimately grounded via a follow-up search_catalog call. Just
    # extract what survived.
    cited: list[int] = []
    for m in CITE_RE.findall(raw):
        pid = int(m)
        if pid not in cited:
            cited.append(pid)
    reply = raw
    if not reply:
        reply = ("I couldn't put together a useful answer for that. Try asking "
                 "about a specific skill or role.")

    courses = await _products_for_cards(set(cited), courses)
    return await _payload(reply, cited, model_used, started, path, courses,
                          message, role_hint, said, history)


async def resume(conversation_id: int, thread_id: str, decision: str,
                 message: str = "") -> dict:
    """Continue a HITL-paused chat turn with the learner's decision.

    `thread_id` is the exact paused run persisted alongside the interrupt
    (`Conversation.pending_interrupt`) — resume targets that specific run,
    not just "the latest turn for this conversation".
    """
    from langgraph.types import Command

    started = time.perf_counter()
    decisions = ([{"type": "approve"}] if decision == "approve"
                else [{"type": "reject", "message": message}] if message
                else [{"type": "reject"}])

    agent = await _get_agent()
    try:
        result = await agent.ainvoke(
            Command(resume={"decisions": decisions}),
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT},
        )
    except Exception as exc:
        log.warning("chat agent resume failed (%s)", exc)
        return {"answer": "Sorry, I couldn't finish that — try asking again.",
               "cited": [], "model": "offline-error",
               "latency_ms": int((time.perf_counter() - started) * 1000),
               "retrieval_path": "none", "courses": [], "pathway": None,
               "intent": {}, "facts": {}}

    history = await _history(conversation_id)
    return await _finish_turn(result, thread_id, started, "none", [], "", "", {}, history)


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
