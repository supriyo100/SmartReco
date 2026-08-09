"""Which provider serves a call, and what happens when one stops working.

The architecture says all LLM traffic goes through Mesh (§7). That stays the
intent — Mesh is tried first for every call — but "mandatory" cannot mean "the
product stops when the account runs out of balance", which is exactly what
happened here: `402 spend_limit_exceeded`, then `403 API key is suspended`, and
every recommendation, chat turn and ingest stopped.

So there are two providers and one rule: **Mesh first, Groq on a provider-level
failure, and never on a prompt-level one.**

That distinction is the whole design. A 402/401/403 means *this provider cannot
serve any request right now* — retrying it is pointless and switching is
correct. A malformed-JSON response or a schema violation is about the prompt,
and re-sending it to a different model just spends money to fail twice. The
same reasoning the outbox drainer already uses to tell fatal from transient
errors (§3.4), applied to model calls.

Groq is OpenAI-wire-compatible, so "switching" is a different client object and
a different model name — not a second code path to keep in sync.

Embeddings are separate, and deliberately so: Groq serves no embedding models
at all. The embedding fallback is local (nomic-embed-text-v1 on CPU), which is
also the only fallback that cannot itself run out of balance. See
`app/agent/embeddings.py`.

A circuit breaker keeps a dead provider from being retried on every call: once
Mesh returns a provider-level error, it is skipped for OUTAGE_COOLDOWN_S before
being tried again. Without it, every chat turn pays a full network round trip
to rediscover the same 402.
"""
from __future__ import annotations

import logging
import re
import time

from openai import AsyncOpenAI

from app.config import settings

log = logging.getLogger("providers")

# Status codes that mean "this provider is unusable", not "this request was
# bad". 429 is deliberately NOT here — it is transient and _chat already backs
# off and retries, which is the right response to rate limiting.
PROVIDER_DOWN_STATUS = {401, 402, 403, 404}

# How long to stop trying a provider after it reports one of the above. Long
# enough that a dead key does not cost a round trip per call; short enough that
# topping up an account takes effect without a restart.
OUTAGE_COOLDOWN_S = 300

_mesh_client: AsyncOpenAI | None = None
_groq_client: AsyncOpenAI | None = None
_ollama_client: AsyncOpenAI | None = None
# provider name → unix time when it may be tried again
_down_until: dict[str, float] = {}


def mesh_client() -> AsyncOpenAI:
    global _mesh_client
    if _mesh_client is None:
        _mesh_client = AsyncOpenAI(base_url=settings.MESH_BASE_URL,
                                   api_key=settings.MESH_API_KEY or "none")
    return _mesh_client


def groq_client() -> AsyncOpenAI:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncOpenAI(base_url=settings.GROQ_BASE_URL,
                                   api_key=settings.GROQ_API_KEY or "none")
    return _groq_client


def ollama_client() -> AsyncOpenAI:
    """Local daemon, OpenAI-wire-compatible. No key — Ollama does not ask for one."""
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = AsyncOpenAI(base_url=settings.OLLAMA_BASE_URL,
                                     api_key="ollama")
    return _ollama_client


def is_provider_down(error: Exception) -> bool:
    """True when the error says the PROVIDER is unusable, not the prompt."""
    status = getattr(error, "status_code", None)
    if status in PROVIDER_DOWN_STATUS:
        return True
    # Some SDK/transport errors carry no status. Connection-level failures are
    # provider-level by definition: nothing about a different prompt would fix
    # a refused connection.
    name = type(error).__name__
    return name in ("APIConnectionError", "APITimeoutError",
                    "InternalServerError")


def mark_down(provider: str, error: Exception | None = None) -> None:
    _down_until[provider] = time.time() + OUTAGE_COOLDOWN_S
    log.warning("provider %s marked down for %ds (%s)", provider,
                OUTAGE_COOLDOWN_S,
                f"{type(error).__name__}: {str(error)[:120]}" if error else "")


def mark_up(provider: str) -> None:
    """Clear a cooldown after a call succeeds — a topped-up account recovers
    on its next success rather than waiting out the full window."""
    if _down_until.pop(provider, None) is not None:
        log.info("provider %s recovered", provider)


def available(provider: str) -> bool:
    return time.time() >= _down_until.get(provider, 0.0)


def reset_breakers() -> None:
    """Test hook. Module-level state would otherwise leak between tests."""
    _down_until.clear()


def chain(kind: str) -> list[tuple[str, AsyncOpenAI, str]]:
    """(provider, client, model) to try in order, for one kind of call.

    `kind` is "fast" | "fast_fallback" | "writer". Providers whose key is
    missing, or which are inside a cooldown, are left out entirely — so an
    empty list means there is nothing to try and the caller must degrade.

    Ollama is the third tier, after Mesh and Groq: opt-in via
    `OLLAMA_ENABLED` (no key needed — a local daemon either answers or it
    doesn't), so it costs nothing to leave configured but disabled.
    """
    models = {
        "fast": (settings.MODEL_FAST, settings.GROQ_MODEL_FAST,
                 settings.OLLAMA_MODEL_FAST),
        "fast_fallback": (settings.MODEL_FAST_FALLBACK,
                          settings.GROQ_MODEL_FAST_FALLBACK,
                          settings.OLLAMA_MODEL_FAST_FALLBACK),
        "writer": (settings.MODEL_WRITER, settings.GROQ_MODEL_WRITER,
                  settings.OLLAMA_MODEL_WRITER),
    }
    mesh_model, groq_model, ollama_model = models.get(kind, models["fast"])

    out: list[tuple[str, AsyncOpenAI, str]] = []
    if settings.MESH_API_KEY and settings.ENV != "test" and available("mesh"):
        out.append(("mesh", mesh_client(), mesh_model))
    if settings.GROQ_API_KEY and settings.ENV != "test" and available("groq"):
        out.append(("groq", groq_client(), groq_model))
    if settings.OLLAMA_ENABLED and settings.ENV != "test" and available("ollama"):
        out.append(("ollama", ollama_client(), ollama_model))
    return out


# Reasoning models emit hidden chain-of-thought before any visible content, so
# a max_tokens sized for the answer alone truncates them to an empty string
# with finish_reason="length". Measured: gpt-oss-120b spends ~45 completion
# tokens reasoning about "reply with: ok"; qwen3.6-27b spends ~170. This floor
# covers the reasoning, and non-reasoning models are unaffected because they
# stop at their natural end well before it.
REASONING_MIN_TOKENS = 1200

# Some models return their chain-of-thought inside `content`, wrapped in
# <think> tags, rather than in a separate `reasoning` field. Qwen3 does this.
# Rendering that verbatim in a chat bubble shows the user the model talking to
# itself about them, which is worse than a bad answer.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S | re.I)
# An unterminated block — the response was cut off mid-thought — leaves an
# opening tag with no close. Everything from it onward is reasoning.
# `<think` not `<think>`: a truncation can land inside the opening tag itself.
_THINK_OPEN_RE = re.compile(r"<think.*", re.S | re.I)


def strip_think_text(content: str, *, model: str = "?") -> str:
    """Remove <think> blocks from a raw content string. The text-level half of
    `strip_reasoning()`, split out so a non-OpenAI-SDK response shape (a
    LangChain `AIMessage`, for the chat agent's model-fallback middleware —
    see app/agent/langchain_bridge.py) can reuse the same rule instead of a
    second regex living in a second file.

    A response that is only reasoning (truncated mid-thought, nothing visible
    ever produced) cleans to empty — there is no answer to keep, so callers
    see the same empty content the raw response effectively had.
    """
    if not content or "<think" not in content.lower():
        return content

    # Closed blocks first, then any unterminated tag left over. Both passes
    # always run: a response can contain a complete <think>…</think> AND a
    # second one truncated by max_tokens, and stopping after the first pass
    # would ship that remainder to the user.
    cleaned = _THINK_OPEN_RE.sub("", _THINK_RE.sub("", content)).strip()
    if not cleaned:
        log.warning("model %s returned only reasoning, no answer", model)
    return cleaned


def strip_reasoning(response) -> None:
    """Remove <think> blocks from a chat completion, in place.

    Done here rather than at each call site because every consumer — chat,
    structured_call, writer_call, the reranker — has the same requirement, and
    one that forgets ships raw chain-of-thought to a user.
    """
    for choice in getattr(response, "choices", []) or []:
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if not content:
            continue
        message.content = strip_think_text(content, model=getattr(response, "model", "?"))


def any_chat_provider() -> bool:
    """Whether a chat call could be served at all.

    Replaces `settings.use_mesh` at call sites that were really asking "can we
    talk to a model?" — a question that now has two possible yeses.
    """
    return bool(chain("fast"))
