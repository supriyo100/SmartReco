"""Bridge between the existing OpenAI-SDK provider chain (app/agent/providers.py)
and the chat agent's LangChain middleware stack (app/chat/agent.py).

`app/agent/providers.py` already distinguishes provider-level failures
(401/402/403/404 + connection errors → switch provider, 5-minute cooldown)
from prompt-level ones (429/5xx → retry the same provider with backoff) — a
real design investment (see that file's docstring) worth keeping rather than
replacing with LangChain's generic `ModelFallbackMiddleware`, which knows
nothing about that distinction or about the circuit breaker.

`MeshFallbackMiddleware` below reuses `providers.chain()` for provider
*selection* and circuit-breaker state, but delegates the actual model call to
`handler(request.override(model=...))` rather than calling the raw
`AsyncOpenAI` clients directly — `ChatOpenAI.ainvoke()` already does the
correct message/tool-call translation for LangChain's agent loop, and
duplicating that by hand would be the exact kind of redundant plumbing this
project's `writer_call`/`structured_call` split was designed to avoid.
`ChatOpenAI` is a thin wrapper over the `openai` SDK, so the exceptions it
raises carry the same `.status_code` that `providers.is_provider_down()`
already knows how to read — no new classification logic needed.
"""
from __future__ import annotations

import asyncio
import logging
import time

from langchain.agents.middleware import wrap_model_call
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from app.config import settings

log = logging.getLogger("agent.langchain_bridge")


def build_chat_models() -> dict[str, ChatOpenAI]:
    """One `ChatOpenAI` per provider tier, keyed the same as `providers.chain()`.

    These never reach the network under a stale or missing key on their own —
    `MeshFallbackMiddleware` is what actually invokes one of them, chosen the
    same way `chain()` already chooses for the non-agentic call sites
    (`mesh.py:_chat()`). `max_tokens` is set higher for Groq/Ollama: both
    currently serve reasoning models (gpt-oss, Qwen3) that spend completion
    tokens on hidden chain-of-thought before any visible content — the same
    `REASONING_MIN_TOKENS` floor `providers.py` already derived for the
    OpenAI-SDK path applies here for the identical reason.
    """
    from app.agent.providers import REASONING_MIN_TOKENS

    return {
        "mesh": ChatOpenAI(base_url=settings.MESH_BASE_URL,
                           api_key=settings.MESH_API_KEY or "none",
                           model=settings.MODEL_WRITER,
                           temperature=0.6, max_tokens=900),
        "groq": ChatOpenAI(base_url=settings.GROQ_BASE_URL,
                           api_key=settings.GROQ_API_KEY or "none",
                           model=settings.GROQ_MODEL_WRITER,
                           temperature=0.6, max_tokens=REASONING_MIN_TOKENS),
        "ollama": ChatOpenAI(base_url=settings.OLLAMA_BASE_URL,
                             api_key="ollama",
                             model=settings.OLLAMA_MODEL_WRITER,
                             temperature=0.6, max_tokens=REASONING_MIN_TOKENS),
    }


def _strip_think(response: ModelResponse) -> ModelResponse:
    """Apply providers.strip_think_text() to the AIMessage LangChain returned.

    Same rule as the OpenAI-SDK path (providers.strip_reasoning), now applied
    to a LangChain `AIMessage` instead of a raw `ChatCompletion` — doubly
    relevant with Ollama serving Qwen models directly.
    """
    from app.agent.providers import strip_think_text

    cleaned_result = []
    for msg in response.result:
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
            cleaned = strip_think_text(msg.content)
            if cleaned != msg.content:
                msg = msg.model_copy(update={"content": cleaned})
        cleaned_result.append(msg)
    if cleaned_result == response.result:
        return response
    return ModelResponse(result=cleaned_result,
                         structured_response=response.structured_response)


@wrap_model_call
async def mesh_fallback_middleware(request: ModelRequest, handler):
    """Provider fallback + retry, ported from `mesh.py:_chat()`.

    Tries each provider `providers.chain()` returns, in order (Mesh, Groq,
    Ollama — the last opt-in). Within a provider: up to 3 attempts with
    exponential backoff on a transient status (429/5xx). Across providers: a
    provider-level failure (401/402/403/404, or a connection/timeout error)
    trips the circuit breaker and moves on immediately, no retry.

    Raises the last error once every provider/attempt is exhausted — caught
    by `OfflineReplyMiddleware` (app/chat/agent_middleware.py), which is
    outside this middleware in the stack and degrades instead of 500ing.
    """
    from app.agent.providers import chain, is_provider_down, mark_down, mark_up
    from app.agent.retry import MAX_ATTEMPTS, RETRY_STATUS, next_delay
    from app.agent.telemetry import record_llm_call

    ctx = request.runtime.context if request.runtime else None
    user_id = getattr(ctx, "user_id", None) if ctx else None
    conversation_id = getattr(ctx, "conversation_id", None) or None if ctx else None

    models = build_chat_models()
    providers = chain("writer")
    if not providers:
        raise RuntimeError("no LLM provider available (no key, or all in cooldown)")

    last_error: Exception | None = None
    for name, _client, model_name in providers:
        chat_model = models.get(name)
        if chat_model is None:
            continue
        delay = 1.0
        for attempt in range(MAX_ATTEMPTS):
            t0 = time.perf_counter()
            print(f"[chat] -> {name} ({model_name}) attempt {attempt + 1}/{MAX_ATTEMPTS} ...",
                  flush=True)
            try:
                response = await handler(request.override(model=chat_model))
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                print(f"[chat] <- {name} responded in {elapsed_ms / 1000:.1f}s", flush=True)
                mark_up(name)
                response = _strip_think(response)
                usage = _usage_from_result(response.result)
                await record_llm_call(
                    kind="chat", provider=name, model=model_name, user_id=user_id,
                    conversation_id=conversation_id, attempt=attempt + 1,
                    is_fallback=(name != providers[0][0]),
                    prompt_tokens=usage[0], completion_tokens=usage[1],
                    total_tokens=usage[2], latency_ms=elapsed_ms)
                return response
            except Exception as e:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                print(f"[chat] x  {name} failed after {elapsed_ms / 1000:.1f}s: "
                      f"{type(e).__name__}: {str(e)[:120]}", flush=True)
                last_error = e
                await record_llm_call(
                    kind="chat", provider=name, model=model_name, user_id=user_id,
                    conversation_id=conversation_id, attempt=attempt + 1,
                    is_fallback=(name != providers[0][0]),
                    latency_ms=elapsed_ms, status="error",
                    error=f"{type(e).__name__}: {e}")
                if is_provider_down(e):
                    mark_down(name, e)
                    break                       # next provider, no retries
                status = getattr(e, "status_code", None)
                if attempt == MAX_ATTEMPTS - 1 or (status is not None and status not in RETRY_STATUS):
                    break                       # give this provider up
                await asyncio.sleep(next_delay(delay, e))
                delay *= 2
        log.warning("provider %s could not serve the chat agent call (%s)", name,
                    type(last_error).__name__ if last_error else "?")

    raise last_error or RuntimeError("all providers failed")


def _usage_from_result(result: list) -> tuple[int, int, int]:
    """(prompt, completion, total) tokens from the AIMessage LangChain
    returned, or zeros if the provider didn't report usage."""
    for msg in reversed(result):
        meta = getattr(msg, "usage_metadata", None)
        if meta:
            return (meta.get("input_tokens", 0) or 0,
                    meta.get("output_tokens", 0) or 0,
                    meta.get("total_tokens", 0) or 0)
    return (0, 0, 0)
