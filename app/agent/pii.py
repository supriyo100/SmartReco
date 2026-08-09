"""Shared PII redaction — regex helpers used by both the chat agent's
PIIMiddleware detectors (app/chat/agent_middleware.py) and the recommendation
writer prompt (app/agent/nodes/generate.py).

Scope, as decided for this product: strip identity-leaking data — email,
phone, physical address, card/account numbers — before it reaches a model
provider (Mesh/Groq/Ollama) or a log line. Target role, skills, goals and
budget text stay intact deliberately: that is the personalization signal the
chat advisor and the recommendation writer exist to use, not PII to hide from
them. Nothing here touches what gets persisted to `ChatMessage` rows — a
user's own saved conversation is not redacted from them.

One module, not one per call site, because a regex that redacts in the chat
prompt but not in the generate.py prompt is a compliance gap wearing a
consistent name.
"""
from __future__ import annotations

import re

# Conservative on purpose: redact-only, never block. A false positive here
# costs a [REDACTED] in a sentence; a false negative costs a leaked identity.
# The asymmetry means the regexes lean wide.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# International-ish: an optional leading +CC, then 7-15 digits with optional
# separators. Deliberately wider than a strict E.164 parser — resumes format
# numbers a dozen ways ("+91 98765-43210", "(022) 2345 6789").
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?)?"
    r"\d{3,5}[\s.\-]?\d{3,5}(?:[\s.\-]?\d{2,4})?(?!\d)"
)

# Payment-card-shaped: 13-19 digits, optionally grouped in 4s with spaces or
# dashes. Deliberately not Luhn-validated — this is redaction, not payment
# processing, and a not-quite-valid-looking card number in a resume is still
# not something to forward to a third-party model.
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")

# Street-address shaped: a leading number followed by 1-5 words and a common
# suffix (St, Street, Ave, Road, Rd, Nagar, Colony, ...). India-aware since
# the catalog and its users are India-priced (₹), but the suffix list stays
# generic enough to catch US/UK-style addresses too.
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}[,]?\s+[A-Za-z0-9.'\-]+(?:\s+[A-Za-z0-9.'\-]+){0,4}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Nagar|Colony|Layout|Marg|"
    r"Sector|Block|Apartments?|Apts?)\b",
    re.IGNORECASE,
)

# Raw pattern strings, for callers that need a regex rather than a redaction
# function — PIIMiddleware's `detector` accepts a pattern string directly
# (app/chat/agent_middleware.py wires these in as custom `phone`/`address`
# PII types; the built-in `email`/`credit_card` types cover the other two).
PHONE_PATTERN = _PHONE_RE.pattern
ADDRESS_PATTERN = _ADDRESS_RE.pattern

REDACTED = "[REDACTED]"


def redact_email(text: str) -> str:
    return _EMAIL_RE.sub(REDACTED, text or "")


def redact_phone(text: str) -> str:
    return _PHONE_RE.sub(REDACTED, text or "")


def redact_card(text: str) -> str:
    return _CARD_RE.sub(REDACTED, text or "")


def redact_address(text: str) -> str:
    return _ADDRESS_RE.sub(REDACTED, text or "")


def redact_pii(text: str) -> str:
    """Apply every redaction in one pass. Order matters: email before phone,
    since an email local-part can otherwise look digit-heavy enough to trip
    the phone pattern once the @domain is stripped."""
    if not text:
        return text or ""
    text = redact_email(text)
    text = redact_address(text)
    text = redact_card(text)
    text = redact_phone(text)
    return text
