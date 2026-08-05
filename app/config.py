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

    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "smartreco"

    @property
    def use_mesh(self) -> bool:  # DeepSeek 4.3: tests run without a key
        return self.ENV != "test" and bool(self.MESH_API_KEY)


settings = Settings()
