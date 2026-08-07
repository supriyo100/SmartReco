"""Provider failover: Mesh first, Groq when Mesh cannot serve, local embeddings.

The property that matters is the distinction between a PROVIDER failure and a
PROMPT failure. Failing over on the first is correct; failing over on the
second spends money to fail twice on the same bad input.
"""
from __future__ import annotations

import pytest

from app.agent import providers
from app.agent.providers import (
    is_provider_down,
    mark_down,
    mark_up,
    reset_breakers,
    strip_reasoning,
)


@pytest.fixture(autouse=True)
def _clean_breakers():
    """Cooldowns are module-level state and would leak between tests."""
    reset_breakers()
    yield
    reset_breakers()


class _Err(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


# --- provider-down classification -------------------------------------------

@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_billing_and_auth_errors_mean_the_provider_is_down(status):
    assert is_provider_down(_Err(status)) is True


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_errors_do_not_condemn_the_provider(status):
    """429 and 5xx are retried against the SAME provider — switching on a rate
    limit would move load to the fallback for a problem that clears itself."""
    assert is_provider_down(_Err(status)) is False


def test_connection_failures_are_provider_level():
    class APIConnectionError(Exception):
        pass

    assert is_provider_down(APIConnectionError("refused")) is True


# --- circuit breaker --------------------------------------------------------

def test_a_down_provider_is_skipped_then_recovers():
    assert providers.available("mesh") is True
    mark_down("mesh", _Err(402))
    assert providers.available("mesh") is False
    # A later success clears it, so topping up an account does not require a
    # restart or a wait for the full cooldown.
    mark_up("mesh")
    assert providers.available("mesh") is True


def test_chain_drops_providers_in_cooldown(monkeypatch):
    monkeypatch.setattr(providers.settings, "ENV", "development")
    monkeypatch.setattr(providers.settings, "MESH_API_KEY", "m")
    monkeypatch.setattr(providers.settings, "GROQ_API_KEY", "g")

    assert [name for name, _, _ in providers.chain("fast")] == ["mesh", "groq"]
    mark_down("mesh", _Err(402))
    assert [name for name, _, _ in providers.chain("fast")] == ["groq"]


def test_chain_uses_each_providers_own_model_names(monkeypatch):
    """Mesh and Groq name the same role differently; one hardcoded model
    string cannot work on both."""
    monkeypatch.setattr(providers.settings, "ENV", "development")
    monkeypatch.setattr(providers.settings, "MESH_API_KEY", "m")
    monkeypatch.setattr(providers.settings, "GROQ_API_KEY", "g")

    models = {name: model for name, _, model in providers.chain("writer")}
    assert models["mesh"] == providers.settings.MODEL_WRITER
    assert models["groq"] == providers.settings.GROQ_MODEL_WRITER


def test_no_keys_means_no_providers(monkeypatch):
    monkeypatch.setattr(providers.settings, "ENV", "development")
    monkeypatch.setattr(providers.settings, "MESH_API_KEY", "")
    monkeypatch.setattr(providers.settings, "GROQ_API_KEY", "")
    assert providers.chain("fast") == []
    assert providers.any_chat_provider() is False


def test_tests_never_reach_a_real_provider():
    """ENV=test must short-circuit the chain even with keys present."""
    assert providers.settings.ENV == "test"
    assert providers.chain("fast") == []


# --- reasoning-model output -------------------------------------------------

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.model = "test-model"


@pytest.mark.parametrize("raw,expected", [
    ("<think>reasoning</think>\n\nThe answer.", "The answer."),
    ("<THINK>upper case</THINK>done", "done"),
    ("no reasoning here", "no reasoning here"),
    # Truncated mid-thought: there is no answer to keep.
    ("<think>cut off half way", ""),
    # Answer first, then a truncated block — keep the answer.
    ("Answer first.<think>then reasoning", "Answer first."),
    # A closed block AND a truncated one; both passes must run.
    ("<think>a</think>Real answer.<think>cut", "Real answer."),
])
def test_think_blocks_never_reach_the_user(raw, expected):
    """Qwen3 returns chain-of-thought inside `content`. Rendering that in a
    chat bubble shows the user the model talking to itself about them."""
    resp = _Resp(raw)
    strip_reasoning(resp)
    assert resp.choices[0].message.content == expected


def test_strip_is_a_noop_without_choices():
    class Empty:
        choices = []
        model = "x"

    strip_reasoning(Empty())      # must not raise


# --- embedding backend ------------------------------------------------------

def test_embedding_cache_is_keyed_per_backend():
    """The dimension trap: a 1536-dim Mesh vector served to a 768-dim nomic
    collection is silent corruption, not a degraded result."""
    from app.agent.embeddings import _cache_key

    assert _cache_key("hello", "mesh") != _cache_key("hello", "local")
    assert _cache_key("hello", "local") == _cache_key("hello", "local")


def test_backend_info_reports_what_will_actually_run(monkeypatch):
    from app.agent import embeddings

    monkeypatch.setattr(embeddings.settings, "EMBED_BACKEND", "local")
    info = embeddings.backend_info()
    assert info["backend"] == "local"
    assert info["dim"] == embeddings.settings.LOCAL_EMBED_DIM
    assert info["model"] == embeddings.settings.LOCAL_EMBED_MODEL


def test_local_backend_needs_no_key(monkeypatch):
    """The whole point of the local fallback: it cannot be turned off by a
    billing event."""
    monkeypatch.setattr(providers.settings, "ENV", "development")
    monkeypatch.setattr(providers.settings, "MESH_API_KEY", "")
    monkeypatch.setattr(providers.settings, "EMBED_BACKEND", "local")
    assert providers.settings.can_embed is True

    from app.agent.embeddings import active_backend
    assert active_backend() == "local"


def test_auto_backend_falls_to_local_when_mesh_is_down(monkeypatch):
    from app.agent.embeddings import active_backend

    monkeypatch.setattr(providers.settings, "ENV", "development")
    monkeypatch.setattr(providers.settings, "MESH_API_KEY", "m")
    monkeypatch.setattr(providers.settings, "EMBED_BACKEND", "auto")
    assert active_backend() == "mesh"

    mark_down("mesh", _Err(402))
    assert active_backend() == "local"
