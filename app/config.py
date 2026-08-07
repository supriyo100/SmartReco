from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MESH_API_KEY: str = ""
    MESH_BASE_URL: str = "https://api.meshapi.ai/v1"
    MODEL_FAST: str = "google/gemini-2.5-flash"
    MODEL_FAST_FALLBACK: str = "openai/gpt-4o-mini"
    MODEL_WRITER: str = "openai/gpt-4o"
    EMBED_MODEL: str = "openai/text-embedding-3-small"

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


settings = Settings()
