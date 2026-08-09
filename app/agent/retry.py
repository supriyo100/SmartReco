"""Shared retry/backoff policy for provider calls — mesh.py's `_chat()` and
langchain_bridge.py's `mesh_fallback_middleware` each ran their own copy of
this loop (fixed 1s/2s exponential, no jitter, no Retry-After) before this
module existed. Duplication is exactly how the old fixed-25 recursion_limit
bug and the three-different-truncation-lengths bug (plan.md §6) both
happened — the same knob tuned twice, differently, by accident.

Why this matters for tokens, not just latency (plan.md §9): a retry re-sends
the entire prompt — there is no partial-credit retry. Backing off badly means
paying for the same prompt tokens more than once for no better a chance of
success.
"""
from __future__ import annotations

import random

RETRY_STATUS = {429, 500, 502, 503}
MAX_ATTEMPTS = 3
# Absolute ceiling on any single sleep, regardless of what a Retry-After
# header asks for or how far exponential backoff has climbed — belt-and-
# suspenders once jitter makes the delay less predictable.
MAX_BACKOFF_S = 5.0


def retry_after_seconds(exc: Exception) -> float | None:
    """Read a Retry-After header off an OpenAI-SDK APIStatusError, if present.

    429 responses commonly carry one; honoring it beats guessing with a fixed
    backoff sequence. Returns None (falls back to exponential backoff) when
    absent or unparsable — additive, never a regression.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def next_delay(base_delay: float, exc: Exception) -> float:
    """The sleep before the next attempt: Retry-After if given, else
    exponential backoff — jittered either way, and capped.

    Jitter (uniform 0.5x-1.5x) matters under concurrency: without it, every
    request retrying the same overloaded provider backs off in lockstep, a
    thundering-herd pattern that makes the rate limit worse right when it
    was just hit.
    """
    delay = retry_after_seconds(exc)
    if delay is None:
        delay = base_delay
    return min(delay * random.uniform(0.5, 1.5), MAX_BACKOFF_S)
