import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MESH_API_KEY: str = ""
    MESH_BASE_URL: str = "https://api.meshapi.ai/v1"
    MODEL_FAST: str = "google/gemini-2.5-flash"
    MODEL_FAST_FALLBACK: str = "openai/gpt-4o-mini"
    MODEL_WRITER: str = "openai/gpt-4o"
    EMBED_MODEL: str = "openai/text-embedding-3-small"

    # --- Groq: the chat fallback when Mesh is down --------------------------
    # Mesh is the primary by architecture (§7, "ALL LLM calls go through
    # Mesh"), but "mandatory" cannot mean "the app stops when the account runs
    # out of balance". Groq is OpenAI-wire-compatible, so the fallback is a
    # different client and model name, not a different code path.
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL_FAST: str = "openai/gpt-oss-120b"
    GROQ_MODEL_FAST_FALLBACK: str = "openai/gpt-oss-20b"
    # Must support strict json_schema — qwen3.6 on Groq is json_mode only.
    GROQ_MODEL_WRITER: str = "openai/gpt-oss-120b"

    # --- Ollama: local fallback when both Mesh and Groq are unavailable -----
    # Opt-in (default off) — a production box has no local daemon, so probing
    # for one on every provider chain build would just add a wasted connection
    # attempt. Turn it on in .env on a machine actually running `ollama serve`.
    # OpenAI-wire-compatible like Groq, so it slots into the same chain() list
    # rather than a separate code path. Needs no API key.
    OLLAMA_ENABLED: bool = False
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL_FAST: str = "qwen3.5:9b"
    OLLAMA_MODEL_FAST_FALLBACK: str = "qwen3.5:9b"
    OLLAMA_MODEL_WRITER: str = "qwen3.6:27b"

    # --- Chat agent middleware: cost and loop guards -------------------------
    # Tightened from 4/6/3: a normal turn needs at most one search-then-answer
    # round plus one clarify-then-retry (SYSTEM_PROMPT rule 11 already tells
    # the model not to blind-retry a rejected search). These are the graceful
    # caps — ModelCallLimitMiddleware ends the turn with whatever answer
    # exists so far rather than crashing, unlike RECURSION_LIMIT below, which
    # is a hard safety net and must stay strictly above these.
    CHAT_MODEL_CALL_LIMIT_PER_TURN: int = 3
    CHAT_TOOL_CALL_LIMIT_PER_TURN: int = 4
    CHAT_SEARCH_CALL_LIMIT_PER_TURN: int = 2

    # --- Chat usage budgets ---------------------------------------------------
    # Mesh ran out of balance mid-build (402 spend_limit_exceeded, no warning
    # — see providers.py) and the first symptom was every chat turn silently
    # failing. These are the guardrail that should have made that "you're
    # near the limit" instead of an outage: checked from `llm_call_log`
    # (app/agent/telemetry.py) before a turn spends anything, at three
    # scopes — one thread, one user's day, and the whole platform's day.
    CHAT_SESSION_TOKEN_LIMIT: int = 20_000
    CHAT_USER_DAILY_TOKEN_LIMIT: int = 50_000
    CHAT_GLOBAL_DAILY_TOKEN_LIMIT: int = 300_000

    # --- Embeddings ---------------------------------------------------------
    # Groq serves no embedding models, so the embedding fallback has to be
    # local. nomic-embed-text-v1 runs on CPU via sentence-transformers: 768
    # dims, no API key, no per-call cost, and it cannot run out of balance.
    #
    # A dimension change invalidates the Chroma collection — vectors from two
    # different models are not comparable — so switching providers means
    # re-ingesting. `EMBED_BACKEND` makes that an explicit choice rather than a
    # silent consequence of a key expiring mid-run.
    #   auto   → Mesh when its key works, else local
    #   mesh   → Mesh only (fail loudly)
    #   local  → local only (no network)
    EMBED_BACKEND: str = "auto"
    LOCAL_EMBED_MODEL: str = "nomic-ai/nomic-embed-text-v1"
    LOCAL_EMBED_DIM: int = 768
    # Public model, so this is optional — it only raises HF Hub rate limits.
    HUGGINGFACE_API_KEY: str = ""

    @property
    def use_groq(self) -> bool:
        return self.ENV != "test" and bool(self.GROQ_API_KEY)

    SECRET_KEY: str = "dev"
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/smartreco.db"
    CHROMA_DIR: str = "./chroma_data"
    ENV: str = "development"

    # A course description reaching a prompt (chat's _fmt_course, generate's
    # _fmt, rerank's _llm_rerank listing) used to be truncated at three
    # different lengths — 280/200/160 — tuned locally at each call site with
    # no shared budget behind the numbers (plan.md §6/§8). One constant now.
    PROMPT_DESCRIPTION_CHARS: int = 200

    RERANK_MODE: str = "fusion"
    FINGERPRINT_COS_THRESHOLD: float = 0.15
    TRIGGER_MIN_EVENTS: int = 8
    TRIGGER_DEBOUNCE_S: int = 90
    REC_STALE_HOURS: int = 6
    COLD_START_MIN_EVENTS: int = 3

    SCHEDULER_ENABLED: bool = True
    DIGEST_HOUR: int = 16
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    # Envelope defaults. MAIL_FROM falls back to SMTP_USER because Gmail
    # rewrites a From: that isn't the authenticated account anyway — setting
    # them apart only produces mail that looks spoofed to the receiver.
    MAIL_FROM: str = ""
    MAIL_FROM_NAME: str = "SmartReco"
    # Absolute base for links inside emails. A relative href is meaningless in
    # an inbox, so every URL the templates emit is built from this.
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    # Re-engagement threshold: days of silence before a user is considered idle.
    REENGAGE_AFTER_DAYS: int = 7

    @property
    def smtp_configured(self) -> bool:
        """True when a real send is possible.

        Absence of credentials is a supported mode, not an error: the mailer
        falls back to writing the rendered message to disk. That keeps tests,
        CI, and a laptop demo working without a secret, and it means a missing
        password degrades to 'the digest is on disk' rather than a stack trace
        inside a scheduler job at 16:00.
        """
        return bool(self.SMTP_HOST and self.SMTP_USER and self.SMTP_PASS)

    @property
    def mail_from(self) -> str:
        return self.MAIL_FROM or self.SMTP_USER or "smartreco@localhost"

    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "smartreco"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    @property
    def use_mesh(self) -> bool:  # DeepSeek 4.3: tests run without a key
        return self.ENV != "test" and bool(self.MESH_API_KEY)

    @property
    def can_embed(self) -> bool:
        """Whether embeddings are possible at all.

        Distinct from `use_mesh`, which several call sites were using to ask
        this question. It is now almost always true: the local backend needs
        no key and no network, so only ENV=test (where model downloads are not
        wanted) and EMBED_BACKEND=mesh-without-a-key say otherwise.
        """
        if self.ENV == "test":
            return False
        mode = (self.EMBED_BACKEND or "auto").lower()
        if mode == "mesh":
            return bool(self.MESH_API_KEY)
        return True     # "local" and "auto" both have a keyless path


settings = Settings()

# pydantic-settings reads .env into `settings` only — it never touches
# os.environ. LangChain/LangSmith's tracing is activated purely by reading
# the process environment at call time, so without this, LANGSMITH_TRACING=1
# in .env silently does nothing (verified: traces never reached the LangSmith
# project despite the key being configured). Both the current (LANGSMITH_*)
# and legacy (LANGCHAIN_*) var names are set since different langchain/
# langsmith versions check different ones.
if settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGSMITH_ENDPOINT", settings.LANGSMITH_ENDPOINT)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT)
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGCHAIN_ENDPOINT", settings.LANGSMITH_ENDPOINT)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
