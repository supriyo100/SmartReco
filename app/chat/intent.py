"""Buy-intent detection — when to surface an offer inside a chat reply.

The ask: "trigger a sell when you feel the user might be interested."

The judgement this module encodes is *when not to*. A career advisor that
pitches on every turn stops being an advisor, and the moment a user decides the
chat is a sales funnel they stop telling it the honest things — their real
budget, their real gaps — which are exactly the inputs that make the advice
good. So the bar is deliberately high and the signal is graded rather than
boolean: the UI shows a soft nudge at `warm` and a real offer only at `hot`.

Scored in Python, not asked of the model. A model asked "is this user ready to
buy?" returns a confident number with no reproducibility, and it would be a
third LLM call on a path that currently makes one. These are the user's own
words, matched literally.

Signals, in descending weight:

  * explicit purchase language ("how do I enrol", "is there a discount")
  * commitment language ("I'm ready", "let's do it", "sign me up")
  * concrete logistics questions — dates, duration, EMI, certificate
  * repeat interest in the same course across turns
  * a stated budget that a retrieved course actually fits inside

Negative signals subtract, and they are the reason this is not a keyword
matcher: "too expensive", "just looking", "maybe later" and outright refusal
must be able to pull a warm conversation back to cold.
"""
from __future__ import annotations

import re

from app.db.models import Product

# --- signal vocabularies ----------------------------------------------------
# Each entry is (pattern, weight). Weights are tuned so that ONE strong signal
# alone reaches `warm` but never `hot` — a single enthusiastic sentence is
# interest, not a decision. `hot` needs corroboration.

# Each row is (name, pattern, weight). The NAME is what gets logged and shown
# to an admin reading why a lead was flagged — a raw regex in that column is
# unreadable, and an explanation nobody can read is not an explanation.
_BUY = [
    ("asked how to enrol",
     r"\b(?:how (?:do|can) i|where do i|want to|like to)\s+"
     r"(?:enrol|enroll|join|sign\s*up|register|buy|purchase|pay)", 0.45),
    ("enrolment language",
     r"\b(?:enrol|enroll|join|sign\s*up|register)\s*(?:me|now|link|process)?\b", 0.30),
    ("asked about discount/EMI",
     r"\b(?:discount|coupon|offer|scholarship|emi|instal?ment|refund)\b", 0.34),
    ("asked the price",
     r"\b(?:how much|what(?:'s| is) the (?:price|fee|cost))\b", 0.30),
    ("payment mechanics", r"\b(?:payment|checkout|invoice|gst)\b", 0.24),
]

_COMMIT = [
    ("said they're ready",
     r"\b(?:i(?:'m| am)\s+(?:ready|in|interested|keen|convinced))\b", 0.34),
    ("explicit go-ahead",
     r"\b(?:let(?:'s| us) do it|sign me up|count me in|"
     r"i(?:'ll| will) take it)\b", 0.42),
    ("positive reaction",
     r"\b(?:sounds? (?:great|good|perfect)|that works|perfect fit)\b", 0.18),
    ("stated they'll start",
     r"\b(?:i(?:'ll| will) (?:start|begin|go with|take))\b", 0.30),
]

_LOGISTICS = [
    ("asked about start date",
     r"\b(?:when does it (?:start|begin)|next (?:batch|cohort)|"
     r"start date|timing|schedule)\b", 0.26),
    ("asked about duration",
     r"\b(?:how long|duration|how many (?:weeks|months|hours))\b", 0.18),
    ("asked about certificate/placement",
     r"\b(?:certificate|placement|job (?:guarantee|assistance)|"
     r"refund policy)\b", 0.22),
    ("checked eligibility",
     r"\b(?:prerequisite|do i need to know|am i eligible|can i handle)\b", 0.16),
    ("asked about format",
     r"\b(?:live|recorded|weekend|weekday)\s+(?:class|session|batch)\b", 0.12),
]

_NEGATIVE = [
    ("price objection",
     r"\b(?:too (?:expensive|costly|much)|can(?:'t| ?not) afford|"
     r"out of (?:my )?budget)\b", -0.55),
    ("just browsing",
     r"\b(?:just (?:looking|browsing|curious)|not (?:ready|now|interested)|"
     r"maybe later)\b", -0.50),
    ("wants free options",
     r"\b(?:free (?:alternative|option|resource|course)s?|"
     r"any(?:thing)? free)\b", -0.30),
    ("declined",
     r"\b(?:no thanks|not for me|don't want|stop (?:selling|pushing))\b", -0.70),
    ("already took it",
     r"\b(?:already (?:did|took|completed|enrolled)|"
     r"i have (?:done|taken))\b", -0.35),
]

_THRESHOLDS = (("hot", 0.75), ("warm", 0.40))


def _match_score(text: str, table: list[tuple[str, str, float]]) -> tuple[float, list[str]]:
    total, hits = 0.0, []
    for name, pattern, weight in table:
        if re.search(pattern, text, re.I):
            total += weight
            hits.append(name)
    return total, hits


def score_intent(message: str, history: list[dict], courses: list[Product],
                 said: dict | None = None) -> dict:
    """Grade purchase intent for this turn.

    Returns a dict the route persists and the UI reads:
        {level, score, reasons, product_id, product_title}

    `level` is "cold" | "warm" | "hot". The UI shows nothing at cold, a soft
    "ready when you are" line at warm, and an enrol CTA at hot.
    """
    text = (message or "").strip()
    result: dict = {"level": "cold", "score": 0.0, "reasons": [],
                    "product_id": None, "product_title": ""}
    if not text:
        return result

    score = 0.0
    reasons: list[str] = []

    for table in (_BUY, _COMMIT, _LOGISTICS):
        value, hits = _match_score(text, table)
        if value:
            score += value
            reasons.extend(hits[:2])

    # Sustained interest: the same course coming back across turns is a
    # stronger signal than any single sentence, because it survived the user
    # thinking about it between messages.
    if courses and history:
        target = courses[0]
        mentions = sum(
            1 for m in history[-12:]
            if m.get("role") == "assistant"
            and f"[[id:{target.id}]]" in (m.get("content") or "")
        )
        if mentions >= 2:
            score += 0.20
            reasons.append(f"discussed {target.title[:32]} across {mentions} turns")

    # Affordability. Only ever adjusts a score that some real signal already
    # started: on its own, "this course is affordable" says nothing about
    # whether the person wants it, and adding it unconditionally made every
    # neutral question ("what is RAG?") carry a spurious purchase reason.
    budget = (said or {}).get("budget_max")
    if budget and courses:
        affordable = [p for p in courses if (p.price or 0) <= budget]
        if not affordable:
            # Everything retrieved is over budget — never pitch, regardless of
            # how enthusiastic the message sounded.
            score -= 0.45
            reasons.append("all candidates over budget")
        elif score > 0:
            score += 0.12
            reasons.append("fits their stated budget")

    # A bare "yes" is agreement only if it is answering something. The last
    # entry in `history` is the previous ASSISTANT turn — the current user
    # message is not in it yet — so a trailing user turn means the thread is
    # mid-question and the affirmative is not a response to a suggestion.
    if re.match(r"^(?:yes|yeah|yep|ok(?:ay)?|sure|do it|please)\b", text, re.I) \
            and len(text.split()) <= 4:
        last_assistant = next((m for m in reversed(history or [])
                               if m.get("role") == "assistant"), None)
        if last_assistant and "[[id:" in (last_assistant.get("content") or ""):
            score += 0.34
            reasons.append("agreed to a specific suggestion")

    negative, neg_hits = _match_score(text, _NEGATIVE)
    if negative:
        score += negative
        reasons.append(f"objection: {', '.join(neg_hits[:2])}")

    score = max(0.0, min(1.0, score))
    level = "cold"
    for name, threshold in _THRESHOLDS:
        if score >= threshold:
            level = name
            break

    # A pitch with no specific course is a banner ad. If we cannot name what
    # they would buy, we have nothing to offer and say nothing.
    if level != "cold" and not courses:
        level = "cold"

    if courses:
        target = courses[0]
        result["product_id"] = target.id
        result["product_title"] = target.title

    result.update({"level": level, "score": round(score, 3), "reasons": reasons})
    return result
