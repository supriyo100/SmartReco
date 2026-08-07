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
