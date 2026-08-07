"""analyze node — refresh the interest vector before ranking uses it.

The original design had this as an LLM call that turned raw events into a
structured interest profile. It is deterministic Python instead, and that is
the §5.1 argument applied to its own first node: `scorer.py` already computes
dual-horizon decayed category weights in closed form, and asking a model to do
the same arithmetic would be slower, non-reproducible, and cost a call on every
run — on the node whose entire job is to decide whether a call is warranted.

Thin by design. The maths is in `scorer.py` (pure, testable with dicts) and the
persistence is in `interests.py`; this is the node boundary that the pipeline
calls, kept separate so the graph reads as retrieve → rank → generate →
validate with analyze in front of it.
"""
from __future__ import annotations

import logging

log = logging.getLogger("agent.analyze")


async def run(user_id: int) -> dict:
    """Recompute and persist interests. Returns the report, including cos_dist."""
    from app.agent.interests import refresh_interests

    report = await refresh_interests(user_id)
    log.debug("analyze user_id=%s events=%s top=%s", user_id,
              report["events"], report["top"])
    return report
