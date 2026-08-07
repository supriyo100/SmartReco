"""grade node — is this set good enough to show, or should retrieval widen?

The one place the pipeline can loop. It scores the ranked set on coverage and
evidence, and asks for a second retrieval round when the answer is weak.

Deterministic, like every other decision in this pipeline. The original design
had an LLM grade its own candidate set, which is the pattern that reliably
returns "yes, these are good" — a model marking its own homework adds a call
and removes a check.

The bar is deliberately low. This exists to catch a genuinely empty result (no
gap coverage, no interest match, nothing but rating priors), not to enforce
quality — a set that is merely mediocre is still better than the empty state,
and a loop that retries until it is happy is how a two-call pipeline becomes a
six-call one.
"""
from __future__ import annotations

import logging

log = logging.getLogger("agent.grade")

# Below this, the set is resting almost entirely on catalog priors rather than
# on anything about this user, and a wider retrieval is worth one more round.
MIN_EVIDENCE = 0.08
MAX_ROUNDS = 2


def evidence_of(ranked: list[dict]) -> float:
    """Mean per-user evidence across the set.

    Only the terms that say something about THIS person count. `rating_prior`
    and `level_fit` are excluded on purpose: both are non-zero for every course
    in the catalog, so including them would let a set with no personalisation
    at all clear the bar.
    """
    if not ranked:
        return 0.0
    total = 0.0
    for row in ranked:
        terms = row.get("terms", {})
        total += (terms.get("gap_match", 0.0)
                  + terms.get("interest_match", 0.0)
                  + terms.get("graph_adjacency", 0.0))
    return total / len(ranked)


def run(ranked: list[dict], facts: dict, round_number: int = 1) -> dict:
    """Returns {ok, evidence, retry, why}."""
    evidence = evidence_of(ranked)

    # A cold-start set is expected to have no evidence — that is what cold
    # start means. Retrying would produce the same catalog spread twice.
    if facts.get("cold_start"):
        return {"ok": True, "evidence": evidence, "retry": False,
                "why": "cold start: catalog spread is the intended output"}

    if evidence >= MIN_EVIDENCE or round_number >= MAX_ROUNDS:
        return {"ok": True, "evidence": round(evidence, 3), "retry": False,
                "why": "sufficient" if evidence >= MIN_EVIDENCE else "round cap"}

    return {"ok": False, "evidence": round(evidence, 3), "retry": True,
            "why": "set rests on catalog priors, not on this user"}
