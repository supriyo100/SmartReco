"""Conversation context: what the model sees, and what retrieval searches for.

A career-advice thread has two horizons, and collapsing them is the mistake
this module exists to avoid — the same dual-horizon reasoning the interest
model uses for behavior (§5.2), applied to conversation.

**Short horizon — the last few turns.** This is what makes "what about the
cheaper one?" resolvable. It is verbatim, because paraphrasing the immediately
preceding turn loses exactly the referent the follow-up depends on.

**Long horizon — everything before that.** A thirty-turn thread cannot be sent
verbatim: the cost of turn N would grow with N, which is the one property §5.1
forbids. But it cannot be dropped either — the user stated their budget in turn
three and expects it to still hold in turn twenty. So the older half is
compacted into a small set of durable facts: constraints they stated, courses
already discussed, and topics raised.

Compaction is deterministic Python, not a summarization call. Two reasons: a
summary call on every turn doubles the LLM cost of chatting, and a model
summarizing "my budget is 5000" as "the user mentioned budget" destroys the
number that made it useful. Regexes over the user's own turns keep the facts
intact.

Retrieval gets a different string from the model. See `retrieval_query`.
"""
from __future__ import annotations

import re

# Verbatim turns. Four exchanges is enough to hold a referent ("the second
# one", "that bootcamp") without the prompt growing unboundedly.
RECENT_TURNS = 8            # messages, i.e. ~4 exchanges
# Turns older than the recent window that get scanned for durable facts.
COMPACT_SCAN = 40

# Money in a user's own words. Deliberately broad on separators and units:
# "5k", "₹5,000", "5000 rupees", "under 10000" all appear in real messages.
_BUDGET_RE = re.compile(
    r"(?:under|below|less than|max(?:imum)?|budget(?:\s+is)?|within|upto|up to|"
    r"around|about)?\s*(?:₹|rs\.?|inr)?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|thousand)?",
    re.I)
# A cue is required before any number is read as money, so "3 years
# experience" and "10 hours a week" cannot become budgets. The range
# prepositions are cues in their own right: "under 10000" states a ceiling as
# plainly as "my budget is 10000" does, and requiring the word "budget" would
# miss the more natural phrasing.
_BUDGET_CUE = re.compile(
    r"budget|afford|cheap|expensive|price|cost|₹|rs\.?|inr|spend|"
    r"\b(?:under|below|less than|within|upto|up to|max(?:imum)?)\b", re.I)

_HOURS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+)?\s*(?:hours?|hrs?|h)\s*(?:a|per|/)?\s*(week|day|weekend)?", re.I)

# Role/goal statements. Anchored on first person so a course title containing
# "engineer" is not mistaken for the user's stated target.
# \b before (?:a|an) so the article is matched as a whole word — without it,
# "an AI engineer" matches `a` and leaves the stray "n" glued to the role,
# producing "n AI engineer".
#
# The captured span is capped at three words. Without that cap the lazy quantifier
# still had to reach a role noun, so "I want to learn RAG and get into GenAI
# engineering" captured the entire clause as the target role. A job title is
# one to three words ("GenAI engineer", "senior data scientist"); anything
# longer is a sentence that happens to contain one.
_ROLE_RE = re.compile(
    r"(?:i(?:'m| am)?\s+|become\s+|move into\s+|transition (?:in)?to\s+|"
    r"want to be\s+|target(?:ing)?\s+(?:role\s+)?(?:is\s+)?)"
    r"(?:\b(?:a|an)\b\s*)?"
    r"((?:[a-z][a-z+.-]*\s+){0,2}"
    r"(?:engineer|developer|scientist|architect|analyst|manager|lead|"
    r"consultant|researcher))\b",
    re.I)
# Verbs that mean the match was a sentence, not a self-description. "want to
# learn RAG and get into GenAI engineering" should not set a target role of
# "into GenAI engineering".
_ROLE_NOISE = {"learn", "learning", "get", "getting", "into", "study", "studying",
               "know", "about", "and", "or", "the", "some", "more", "start"}

# Topic nouns worth carrying forward. Curated rather than derived: an
# open-vocabulary extractor would carry "thing" and "stuff" into the prompt.
_TOPICS = (
    "rag", "langgraph", "langchain", "llamaindex", "agentic", "agents", "mlops",
    "llmops", "fine-tuning", "finetuning", "embeddings", "vector database",
    "prompt engineering", "deployment", "kubernetes", "docker", "aws", "gcp",
    "azure", "python", "sql", "machine learning", "deep learning", "nlp",
    "computer vision", "data science", "statistics", "mcp", "evaluation",
    "observability", "multimodal", "transformers", "llm", "genai", "no-code",
)


def split_horizons(history: list[dict]) -> tuple[list[dict], list[dict]]:
    """(older, recent). `recent` goes to the model verbatim."""
    if len(history) <= RECENT_TURNS:
        return [], history
    return history[:-RECENT_TURNS], history[-RECENT_TURNS:]


def _norm_amount(number: str, unit: str | None) -> float | None:
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if unit and unit.lower() in ("k", "thousand"):
        value *= 1000
    # Bound it. A bare "3" in "I have 3 years experience" must not become a
    # ₹3 budget that filters the entire catalog out of every answer.
    return value if 500 <= value <= 1_000_000 else None


def extract_facts(messages: list[dict]) -> dict:
    """Durable facts from the user's own turns, newest wins.

    Only `role == "user"` is scanned. The assistant's turns quote prices and
    role names constantly, and treating those as user statements would let the
    advisor's own suggestion become the user's stated budget.
    """
    facts: dict = {"budget_max": None, "weekly_hours": None,
                   "target_role": "", "topics": []}
    topics: list[str] = []

    for message in messages:
        if message.get("role") != "user":
            continue
        text = (message.get("content") or "")
        lowered = text.lower()

        if _BUDGET_CUE.search(text):
            for number, unit in _BUDGET_RE.findall(text):
                amount = _norm_amount(number, unit)
                if amount is not None:
                    facts["budget_max"] = amount      # last mention wins
                    break

        hours = _HOURS_RE.search(text)
        if hours:
            try:
                count = int(hours.group(1))
                period = (hours.group(2) or "week").lower()
                if period == "day":
                    count *= 7
                if 1 <= count <= 80:
                    facts["weekly_hours"] = count
            except ValueError:
                pass

        role = _ROLE_RE.search(text)
        if role:
            # Trim leading filler so "into GenAI engineering" becomes "GenAI
            # engineering" rather than being stored with the preposition.
            words = role.group(1).strip().split()
            while words and words[0].lower() in _ROLE_NOISE:
                words.pop(0)
            if words:
                facts["target_role"] = " ".join(words)[:60]

        for topic in _TOPICS:
            if topic in lowered and topic not in topics:
                topics.append(topic)

    facts["topics"] = topics[-10:]
    return facts


def compact(older: list[dict]) -> str:
    """Older turns → a few lines of durable fact. "" when there is nothing.

    This is what keeps the prompt flat as a thread grows: whether turn 30 or
    turn 300, this block stays roughly the same size.
    """
    if not older:
        return ""
    facts = extract_facts(older[-COMPACT_SCAN:])
    lines: list[str] = []
    if facts["target_role"]:
        lines.append(f"They said earlier they are aiming for: {facts['target_role']}")
    if facts["budget_max"] is not None:
        lines.append(f"They stated a budget of about ₹{int(facts['budget_max']):,}")
    if facts["weekly_hours"]:
        lines.append(f"They have about {facts['weekly_hours']} hours a week")
    if facts["topics"]:
        lines.append("Topics already raised: " + ", ".join(facts["topics"]))

    # Courses already discussed, so the advisor does not re-pitch them as new.
    cited: list[str] = []
    for message in older:
        if message.get("role") == "assistant":
            for pid in re.findall(r"\[\[id:(\d+)\]\]", message.get("content") or ""):
                if pid not in cited:
                    cited.append(pid)
    if cited:
        lines.append("Courses already discussed (ids): " + ", ".join(cited[-8:]))

    if not lines:
        return ""
    return ("EARLIER IN THIS CONVERSATION (compacted — do not repeat back):\n"
            + "\n".join(f"- {line}" for line in lines))


def retrieval_query(message: str, recent: list[dict], target_role: str = "",
                    topics: list[str] | None = None) -> str:
    """The string the retrievers search for — not the string the model reads.

    Retrieval and reranking want different things, so they get different text.
    Recall wants breadth: "what about the cheaper one?" contains no retrievable
    noun at all, and without the previous turn's subject it retrieves nothing.
    So the query is widened with the last user turn, the target role, and
    carried-forward topics.

    The reranker deliberately does NOT get this string — see `retrieve`.
    Widening helps recall and hurts precision, so the widened text finds
    candidates and the user's actual question orders them.
    """
    parts = [(message or "").strip()]

    # A short follow-up is the case that needs the previous turn most. Long
    # messages carry their own nouns and get diluted by prepending history.
    if len(parts[0].split()) <= 6:
        for message_ in reversed(recent):
            if message_.get("role") == "user":
                previous = (message_.get("content") or "").strip()
                if previous and previous != parts[0]:
                    parts.append(previous[:200])
                break

    if target_role:
        parts.append(target_role)
    if topics:
        parts.extend(topics[-3:])

    return " ".join(p for p in parts if p)[:600]
