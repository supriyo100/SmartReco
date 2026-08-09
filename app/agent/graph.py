"""The recommendation pipeline — retrieve → rank → generate → validate → store.

Written as a plain async function rather than a LangGraph `StateGraph`, and
that is a deliberate revision of the original design note (which specified
LangGraph with a SqliteSaver checkpointer). The reasoning:

  * The graph is linear. There is no branching, no cycle, no human-in-the-loop
    pause and no resume-from-checkpoint requirement — every node runs exactly
    once in a fixed order. A framework whose value is orchestrating non-linear
    state buys nothing here and costs a dependency, a serialization boundary,
    and a layer of indirection between a traceback and the line that raised.
  * Checkpointing a run that takes ~2s and can be re-run idempotently is
    solving a problem this pipeline does not have.

What LangGraph would have given us that matters — a record of which nodes ran
and what they cost — is written to `agent_runs` directly, which is the thing
`/admin/agent-runs` reads anyway.

This reasoning is scoped to *this* pipeline, not to LangGraph/LangChain in
general — `app/chat/agent.py` does use LangChain's `create_agent` with a real
middleware stack (PII redaction, call limits, tool retry, human-in-the-loop,
provider fallback), because the chat agent is the opposite case on every
point above: the model decides whether to search again or ask a question,
the tool set is open-ended, and a human sometimes needs to weigh in
mid-turn. Same judgment, opposite conclusion, because the two problems
differ in kind. See the architecture doc §13.3.

The pipeline is idempotent per user: it demotes the previous `is_current` row
and inserts a new one in a single transaction, so a concurrent second run
cannot leave two current sets.
"""
from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import update

from app.config import settings
from app.db.models import AgentRun, Recommendation
from app.db.session import async_session

log = logging.getLogger("agent.graph")

TOP_K = 5


async def generate_recommendations(user_id: int, trigger_reason: str = "manual",
                                   top_k: int = TOP_K) -> dict:
    """Run the pipeline for one user and store the result.

    Returns a report. Never raises: a failure is logged to `agent_runs` with
    status="error" and the user keeps whatever set they already had, which is
    strictly better than replacing it with nothing.
    """
    from app.agent.nodes import analyze, fusion_rank, generate, grade, retrieve, validate
    from app.agent.triggers import lock_for

    started = time.perf_counter()
    trace_id = uuid.uuid4().hex[:12]
    node_path: list[str] = []
    report: dict = {"user_id": user_id, "trigger": trigger_reason,
                    "trace_id": trace_id, "stored": False}

    # The per-user lock is what makes "one current set" true under concurrency:
    # a browsing trigger and a profile-save trigger can fire within the same
    # second, and both would otherwise demote each other's row.
    async with lock_for(user_id):
        try:
            # analyze first: ranking reads `interests`, so a stale vector would
            # score this run against last week's behavior.
            node_path.append("analyze")
            await analyze.run(user_id)

            node_path.append("retrieve")
            candidates, facts = await retrieve.run(user_id)
            report["candidates"] = len(candidates)
            report["queries"] = facts.get("queries", [])
            if not candidates:
                report["skipped"] = "nothing retrievable"
                await _record_run(user_id, trace_id, node_path, trigger_reason,
                                  started, "empty", 0)
                return report

            node_path.append("fusion_rank")
            ranked = await fusion_rank.run(user_id, candidates, facts, top_k=top_k)
            if not ranked:
                report["skipped"] = "everything filtered out"
                await _record_run(user_id, trace_id, node_path, trigger_reason,
                                  started, "empty", 0)
                return report

            # grade: one widening round if the set rests on catalog priors
            # rather than on anything about this user. Deterministic, so this
            # loop cannot run away — MAX_ROUNDS caps it at two.
            node_path.append("grade")
            verdict = grade.run(ranked, facts, round_number=1)
            report["evidence"] = verdict["evidence"]
            if verdict["retry"]:
                node_path.append("retrieve")
                wider, facts = await retrieve.run(user_id)
                extra = await fusion_rank.run(user_id, wider, facts,
                                              top_k=top_k)
                if extra:
                    ranked = extra
                    node_path.append("grade")
                    verdict = grade.run(ranked, facts, round_number=2)
                    report["evidence"] = verdict["evidence"]

            node_path.append("generate")
            written = await generate.run(user_id, ranked, facts)
            llm_calls = 0 if written.get("fallback") else 1

            node_path.append("validate")
            allowed = {row["product"].id for row in ranked}
            items, narrative, problems = await validate.run(
                written["items"], allowed, written.get("narrative", ""))
            report["validation_problems"] = problems
            if not items:
                report["skipped"] = "validation removed everything"
                await _record_run(user_id, trace_id, node_path, trigger_reason,
                                  started, "invalid", llm_calls)
                return report

            node_path.append("store")
            rec_id = await _store(user_id, narrative, items, facts,
                                  written.get("model_used", ""), trigger_reason)

            report.update({
                "stored": True, "recommendation_id": rec_id,
                "items": len(items), "model": written.get("model_used", ""),
                "fallback": bool(written.get("fallback")),
                "narrative": narrative,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            })
            await _record_run(user_id, trace_id, node_path, trigger_reason,
                              started, "ok", llm_calls,
                              fallback=bool(written.get("fallback")))
            log.info("recommendations for user_id=%s: %d items (%s, %s)",
                     user_id, len(items), trigger_reason,
                     written.get("model_used"))
            return report

        except Exception as exc:
            log.exception("recommendation pipeline failed for user_id=%s", user_id)
            report["error"] = f"{type(exc).__name__}: {exc}"
            await _record_run(user_id, trace_id, node_path, trigger_reason,
                              started, "error", 0)
            return report


async def _store(user_id: int, narrative: str, items: list[dict], facts: dict,
                 model: str, trigger_reason: str) -> int:
    """Demote the old current set and insert the new one, in one transaction.

    Old sets are kept, not deleted: `is_current` is the same pattern
    `resume_analyses` uses, and history is what makes "did the recommendation
    change after I uploaded a resume" an answerable question.
    """
    from app.agent.scorer import fingerprint
    from app.chat.brief import refresh_background_brief

    async with async_session() as s:
        await s.execute(
            update(Recommendation)
            .where(Recommendation.user_id == user_id,
                   Recommendation.is_current.is_(True))
            .values(is_current=False)
        )
        row = Recommendation(
            user_id=user_id, narrative=narrative, items=items,
            fingerprint=fingerprint(facts.get("interests") or {},
                                    "", str(facts.get("experience_years") or "")),
            trigger_reason=trigger_reason, model_used=model, is_current=True,
        )
        s.add(row)
        await s.flush()
        # A fresh recommendation set is new material for "ALREADY RECOMMENDED"
        # in the chat brief — re-render it now rather than leaving the chat
        # agent to notice the fingerprint mismatch on the next turn.
        await refresh_background_brief(s, user_id)
        await s.commit()
        await s.refresh(row)
        return row.id


async def _record_run(user_id: int, trace_id: str, node_path: list[str],
                      trigger_reason: str, started: float, status: str,
                      llm_calls: int, fallback: bool = False) -> None:
    """One row per run in `agent_runs` — what /admin/agent-runs reads.

    Written for every outcome including failures. A table that only records
    successes cannot answer "why did this user never get recommendations",
    which is the question it exists for.
    """
    try:
        async with async_session() as s:
            s.add(AgentRun(
                user_id=user_id, trace_id=trace_id, node_path=node_path,
                retrieval_rounds=node_path.count("retrieve"),
                llm_calls=llm_calls, fallback_used=fallback,
                trigger_reason=trigger_reason, status=status,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ))
            await s.commit()
    except Exception:
        # Observability must never break the thing it observes.
        log.warning("could not record agent_run for user_id=%s", user_id)


async def maybe_generate(user_id: int, reason_hint: str = "") -> dict:
    """Consult the planner, then run only if it says so (§5.2).

    This is the entry point every caller should use — profile saves, the
    tracking queue, the chat route. `generate_recommendations` bypasses the
    policy and exists for the admin "generate now" button.
    """
    from app.agent.triggers import should_run

    # Never under test. The pipeline is fired-and-forgotten from request
    # handlers, so a task outliving its test reaches the database during
    # teardown and fails a test that has already passed. Tests that DO want the
    # pipeline call `generate_recommendations` directly.
    if settings.ENV == "test":
        return {"ran": False, "reason": "test_env", "diagnostics": {}}

    run, reason, diagnostics = await should_run(user_id, reason_hint)
    if not run:
        log.debug("no run for user_id=%s: %s %s", user_id, reason, diagnostics)
        return {"ran": False, "reason": reason, "diagnostics": diagnostics}

    report = await generate_recommendations(user_id, trigger_reason=reason)
    report["ran"] = True
    report["reason"] = reason
    return report
