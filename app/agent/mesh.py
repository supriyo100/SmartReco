"""Single gateway for all Mesh API calls: chat, structured chat, embeddings.

▲A2  structured_call: MODEL_FAST first; on parse failure retry once with
     MODEL_FAST_FALLBACK (gpt-4o-mini — reliable json_schema). The v1 trap
     list knew response_format fails silently; this is the runtime answer.
▲A5  embed_batch: one POST per batch, read-through EmbeddingCache.
"""
import asyncio
import hashlib
import json
import logging
import time

from openai import AsyncOpenAI

from app.config import settings

log = logging.getLogger("mesh")

client = AsyncOpenAI(base_url=settings.MESH_BASE_URL, api_key=settings.MESH_API_KEY or "none")


def _parse_json(raw: str) -> dict:
    """▲B1 last line of defense: some models return prose-wrapped or
    fence-wrapped JSON even under response_format. Strip markdown fences,
    then extract the outermost {...} before parsing."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        txt = txt[4:] if txt.lower().startswith("json") else txt
        txt = txt.strip().rstrip("`").strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start == -1 or end == -1:
        raise json.JSONDecodeError("no JSON object found", raw, 0)
    return json.loads(txt[start:end + 1])


async def _chat(model: str, messages: list, kind: str = "fast",
                user_id: int | None = None, **kw):
    """One chat call, Mesh first and Groq if Mesh cannot serve it.

    `model` is kept for callers that name a model explicitly, but the provider
    chain owns model selection: Mesh and Groq have different model names for
    the same role, so a single hardcoded name cannot work on both. Pass `kind`
    ("fast" | "fast_fallback" | "writer") and let `chain()` resolve it.

    Two failure classes, handled differently — the distinction is the point:
      * 429/5xx  → transient. Back off and retry the SAME provider.
      * 401/402/403 → the provider is unusable. Stop retrying it, mark it
        down so the next call skips it, and try the next provider.

    Every attempt — success or failure — is logged to `llm_call_log`
    (app/agent/telemetry.py) with token usage where the provider returned it,
    so a token/cost dashboard sees the same fallback path this function took.
    """
    from app.agent.providers import (
        REASONING_MIN_TOKENS,
        chain,
        is_provider_down,
        mark_down,
        mark_up,
        strip_reasoning,
    )
    from app.agent.retry import MAX_ATTEMPTS, RETRY_STATUS, next_delay
    from app.agent.telemetry import record_llm_call

    providers = chain(kind)
    if not providers:
        raise RuntimeError("no LLM provider available (no key, or all in cooldown)")

    last_error: Exception | None = None
    for name, provider_client, provider_model in providers:
        chosen = provider_model if kind else model
        call_kw = dict(kw)
        # Reasoning models (Groq's gpt-oss, Qwen3) spend completion tokens on
        # hidden reasoning BEFORE producing any content. A max_tokens sized for
        # the visible answer truncates them mid-thought and returns an empty
        # string with finish_reason="length" — a silent empty reply, not an
        # error. Raise the ceiling for those models rather than lowering it
        # everywhere.
        if "max_tokens" in call_kw and call_kw["max_tokens"] is not None:
            call_kw["max_tokens"] = max(int(call_kw["max_tokens"]),
                                        REASONING_MIN_TOKENS)
        delay = 1.0
        for attempt in range(MAX_ATTEMPTS):
            t0 = time.perf_counter()
            try:
                resp = await provider_client.chat.completions.create(
                    model=chosen, messages=messages, **call_kw)
                mark_up(name)
                strip_reasoning(resp)
                usage = getattr(resp, "usage", None)
                await record_llm_call(
                    kind=kind, provider=name, model=resp.model, user_id=user_id,
                    attempt=attempt + 1, is_fallback=(name != providers[0][0]),
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    latency_ms=int((time.perf_counter() - t0) * 1000))
                return resp
            except Exception as e:
                last_error = e
                await record_llm_call(
                    kind=kind, provider=name, model=chosen, user_id=user_id,
                    attempt=attempt + 1, is_fallback=(name != providers[0][0]),
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    status="error", error=f"{type(e).__name__}: {e}")
                if is_provider_down(e):
                    mark_down(name, e)
                    break                       # next provider, no retries
                status = getattr(e, "status_code", None)
                if attempt == MAX_ATTEMPTS - 1 or (status is not None and status not in RETRY_STATUS):
                    break                       # give this provider up
                await asyncio.sleep(next_delay(delay, e))
                delay *= 2
        log.warning("provider %s could not serve the call (%s)", name,
                    type(last_error).__name__ if last_error else "?")

    raise last_error or RuntimeError("all providers failed")


async def structured_call(messages: list, schema: dict,
                          schema_name: str = "out") -> tuple[dict, str, bool]:
    """Returns (parsed, model_used, fallback_used)."""
    rf = {"type": "json_schema",
          "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
    try:
        resp = await _chat(settings.MODEL_FAST, messages, kind="fast",
                           response_format=rf, temperature=0)
        return _parse_json(resp.choices[0].message.content), resp.model, False
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as e:
        # A PARSE failure, which is about the prompt and the model's habits —
        # not about the provider. Retrying on a different model is the right
        # response; provider-level errors never reach here, because _chat has
        # already failed over and would have raised something else.
        log.warning("structured parse failed (%s) → retrying on the fallback model", e)
    resp = await _chat(settings.MODEL_FAST_FALLBACK, messages,
                       kind="fast_fallback", response_format=rf, temperature=0)
    return _parse_json(resp.choices[0].message.content), resp.model, True


async def writer_call(messages: list, schema: dict) -> tuple[dict, str]:
    """Persuasive generation under a strict schema (arch v1 §5.4 generate)."""
    rf = {"type": "json_schema",
          "json_schema": {"name": "recommendation", "schema": schema, "strict": True}}
    resp = await _chat(settings.MODEL_WRITER, messages, kind="writer",
                       response_format=rf, temperature=0.7)
    # The model that actually answered, which is not necessarily the one asked
    # for — `agent_runs.model_used` must record what ran, not what we intended.
    return _parse_json(resp.choices[0].message.content), resp.model


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_batch(texts: list[str], *,
                      is_query: bool = False) -> list[list[float]]:
    """▲A5: one call for the whole batch, with read-through cache.

    Delegates to app/agent/embeddings.py, which owns backend selection and the
    Mesh→local failover. Kept here as the import site every caller already
    uses, so adding the fallback did not mean editing every call site.
    """
    from app.agent.embeddings import embed_batch as _embed

    return await _embed(texts, is_query=is_query)


async def check_models_at_startup():
    """Verify every configured model is actually offered by Mesh. Don't assume.

    Uses httpx rather than client.models.list(): Mesh returns a bare JSON array,
    not OpenAI's {"object": "list", "data": [...]} envelope, so the SDK's
    pagination wrapper raises trying to read .data. That failure was swallowed
    by the except below — the check ran, logged a warning, and verified nothing.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{settings.MESH_BASE_URL.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.MESH_API_KEY}"},
            )
            resp.raise_for_status()
            payload = resp.json()
        # Accept both shapes, so this keeps working if Mesh adopts the envelope.
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        ids = {r.get("id") for r in rows if isinstance(r, dict)}
    except Exception as e:
        log.warning("could not list Mesh models at startup: %s", e)
        return

    missing = []
    for m in (settings.MODEL_FAST, settings.MODEL_FAST_FALLBACK,
              settings.MODEL_WRITER, settings.EMBED_MODEL):
        if m in ids:
            log.info("mesh model %s: available", m)
        else:
            missing.append(m)
            log.warning("mesh model %s: NOT LISTED among %d models — verify", m, len(ids))
    if not missing:
        log.info("mesh: all %d configured models available", 4)
