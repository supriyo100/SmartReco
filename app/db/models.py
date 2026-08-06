from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="user")  # user | admin
    digest_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String, index=True)
    level: Mapped[str] = mapped_column(String)          # beginner|intermediate|advanced
    price: Mapped[float] = mapped_column(Float)
    tags: Mapped[dict] = mapped_column(JSON, default=list)
    prereq_ids: Mapped[dict] = mapped_column(JSON, default=list)   # ▲A10
    related_ids: Mapped[dict] = mapped_column(JSON, default=list)  # ▲A10
    instructor: Mapped[str] = mapped_column(String, default="")
    rating: Mapped[float] = mapped_column(Float, default=4.5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_uuid: Mapped[str] = mapped_column(String, unique=True)  # client idempotency
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    session_id: Mapped[str] = mapped_column(String)
    # page_view product_view product_dwell search search_result_click
    # category_filter add_to_wishlist cta_click scroll_depth
    # conversion rec_click rec_dismiss                       (▲A8)
    event_type: Mapped[str] = mapped_column(String)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    dwell_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_events_user_ts", "user_id", "ts"),
                      Index("ix_events_session_ts", "session_id", "ts"))


class UserProfile(Base):
    """Two kinds of signal about a person, deliberately in one row.

    The `interests*` / `fingerprint` / `events_seen` fields are DERIVED — the
    interest model writes them from behavior. Everything under "declared" below
    is STATED — the user typed it or uploaded it. They are kept apart because
    they age differently and are trusted differently: behavior is current but
    narrow, a resume is broad but stale the day after it is written. The agent
    reads declared fields for cold-start grounding (a new account has no
    behavior at all) and behavioral fields once they exist.
    """
    __tablename__ = "user_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    interests: Mapped[dict] = mapped_column(JSON, default=dict)        # long horizon
    interests_short: Mapped[dict] = mapped_column(JSON, default=dict)  # ▲A7 session horizon
    price_band: Mapped[str] = mapped_column(String, default="")
    stage: Mapped[str] = mapped_column(String, default="")
    llm_summary: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String, default="")
    events_seen: Mapped[int] = mapped_column(Integer, default=0)

    # --- declared (user-supplied) -------------------------------------------
    full_name: Mapped[str] = mapped_column(String, default="")
    headline: Mapped[str] = mapped_column(String, default="")   # "Backend dev, 3y"
    bio: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[str] = mapped_column(Text, default="")        # what they want next
    skills: Mapped[dict] = mapped_column(JSON, default=list)    # ["python", "sql"]
    experience_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Resume: the extracted TEXT is what the agent can use, so it is stored
    # alongside the file. Keeping only the file would mean re-parsing a PDF on
    # every read; keeping only the text would lose the artifact the user gave us.
    resume_filename: Mapped[str] = mapped_column(String, default="")
    resume_path: Mapped[str] = mapped_column(String, default="")
    resume_text: Mapped[str] = mapped_column(Text, default="")
    resume_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    narrative: Mapped[str] = mapped_column(Text)
    # items: [{product_id, reason, hook, confidence, next_step_id, rank}]  ▲A9
    items: Mapped[dict] = mapped_column(JSON, default=list)
    fingerprint: Mapped[str] = mapped_column(String)
    trigger_reason: Mapped[str] = mapped_column(String, default="")
    model_used: Mapped[str] = mapped_column(String, default="")
    token_cost: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_recs_user_current", "user_id", "is_current"),)


class VectorOutbox(Base):
    __tablename__ = "vector_outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer)
    op: Mapped[str] = mapped_column(String)  # upsert | delete
    status: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # ▲A6 composite index created in init_db.py


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String, default="")
    node_path: Mapped[dict] = mapped_column(JSON, default=list)
    retrieval_rounds: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)  # ▲A2 visibility
    cos_dist: Mapped[float] = mapped_column(Float, default=0.0)          # ▲B5 backs the 0.15 threshold
    trigger_reason: Mapped[str] = mapped_column(String, default="")      # ▲B5
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)      # ▲B5
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DigestLog(Base):
    __tablename__ = "digest_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    sent_date: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="sent")
    __table_args__ = (UniqueConstraint("user_id", "sent_date"),)


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    id: Mapped[int] = mapped_column(primary_key=True)
    text_hash: Mapped[str] = mapped_column(String, unique=True)
    vector: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
