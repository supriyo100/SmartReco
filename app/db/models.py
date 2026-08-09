from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """The account. Identity and access only.

    Everything a user *tells us about themselves* lives in UserProfile, not
    here: this row is read on every authenticated request by
    identity_middleware, so widening it makes every request more expensive to
    serve a field almost nothing reads. The split is deliberate.
    """
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="user")  # user | admin
    digest_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    # `target_role` is the single highest-value declared field: it is what the
    # ATS scores against and what the chatbot steers toward. Without it the
    # resume can only be judged against a guess.
    target_role: Mapped[str] = mapped_column(String, default="")
    current_role: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    phone: Mapped[str] = mapped_column(String, default="")
    linkedin_url: Mapped[str] = mapped_column(String, default="")
    github_url: Mapped[str] = mapped_column(String, default="")
    education: Mapped[str] = mapped_column(String, default="")
    # Budget and commitment are filter facts (T1, §2) — they belong to the
    # user, not the catalog, and they gate which courses can honestly be
    # recommended at all.
    budget_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    weekly_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_mode: Mapped[str] = mapped_column(String, default="")  # live|self-paced|any
    # Resume: the extracted TEXT is what the agent can use, so it is stored
    # alongside the file. Keeping only the file would mean re-parsing a PDF on
    # every read; keeping only the text would lose the artifact the user gave us.
    resume_filename: Mapped[str] = mapped_column(String, default="")
    resume_path: Mapped[str] = mapped_column(String, default="")
    resume_text: Mapped[str] = mapped_column(Text, default="")
    resume_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # The rendered "WHAT YOU KNOW ABOUT THIS USER" chat prompt block (see
    # app/chat/brief.py), stored at write-time rather than rebuilt from this
    # row + ResumeAnalysis + Recommendation on every chat turn — plan.md §1-5.
    # `brief_fingerprint` is a hash of exactly the fields the brief reads;
    # a mismatch means it's stale and the chat agent falls back to rendering
    # it live rather than serving outdated advice.
    background_brief: Mapped[str] = mapped_column(Text, default="")
    brief_fingerprint: Mapped[str] = mapped_column(String, default="")

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)


class ResumeAnalysis(Base):
    """One ATS run over one resume, scored against one target role.

    A separate table rather than columns on user_profiles, for two reasons.
    First, a user re-runs this: after editing a resume they want to see whether
    the score moved, and history is the only way to answer that. Second, the
    same resume scores differently against different target roles, so the
    (resume, role) pair is the real key — that does not fit in a 1:1 row.

    `is_current` marks the latest run per user, the same pattern
    `recommendations` uses, so the profile page is one indexed lookup.
    """
    __tablename__ = "resume_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_role: Mapped[str] = mapped_column(String, default="")
    # 0-100. Composite of the four sub-scores below; see app/profiles/ats.py
    # for the weighting, which is deterministic Python, never asked of an LLM.
    ats_score: Mapped[int] = mapped_column(Integer, default=0)
    keyword_score: Mapped[int] = mapped_column(Integer, default=0)
    structure_score: Mapped[int] = mapped_column(Integer, default=0)
    experience_score: Mapped[int] = mapped_column(Integer, default=0)
    readability_score: Mapped[int] = mapped_column(Integer, default=0)
    # The gap list is the load-bearing output: it is what turns "your resume
    # scores 62" into a recommendation for a specific course.
    matched_skills: Mapped[dict] = mapped_column(JSON, default=list)
    missing_skills: Mapped[dict] = mapped_column(JSON, default=list)
    # Structured extraction — contact block, sections found, work history.
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    # Non-fatal problems worth showing: no dates on a role, no contact email,
    # a section an ATS parser would not find.
    warnings: Mapped[dict] = mapped_column(JSON, default=list)
    suggestions: Mapped[dict] = mapped_column(JSON, default=list)
    resume_chars: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_ats_user_current", "user_id", "is_current"),)


class Conversation(Base):
    """A chat thread. The architecture doc said conversation memory was not
    built because 'users browse, they never chat with the agent' — that is no
    longer true of this product, so the table exists and the reason is stated
    rather than the old line being quietly deleted.

    `session_id` ties a thread to the browser session that opened it, the same
    id `events` carries. That is what lets a conversation be correlated with
    what the person was *browsing* while they had it — the two strongest
    signals about intent, currently living in separate tables with no join.

    `tenant_id` is a partition key, defaulted rather than nullable. Retrofitting
    multi-tenancy is a migration across every table that holds user data;
    carrying the column from the start costs one indexed integer and means the
    query patterns are already tenant-scoped when a second tenant appears.

    `user_snapshot` freezes what was known about the person when the thread
    started — target role, budget, ATS gaps. Advice is only judgeable against
    the facts it was given, and those facts change: a reply that looks wrong
    today may have been right for a profile that has since been edited.
    """
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    session_id: Mapped[str] = mapped_column(String, default="", index=True)
    title: Mapped[str] = mapped_column(String, default="")
    # Facts mined from the thread itself (budget, target role, topics) — see
    # app/chat/context.py. Written back each turn so they survive the thread.
    derived_facts: Mapped[dict] = mapped_column(JSON, default=dict)
    user_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # Highest buy-intent seen in this thread, so a warm lead stays visible
    # after the turn that produced it scrolls away.
    intent_level: Mapped[str] = mapped_column(String, default="cold")
    intent_score: Mapped[float] = mapped_column(Float, default=0.0)
    # Set when HumanInTheLoopMiddleware pauses a turn for approval (e.g. a
    # profile write the agent inferred from conversation) and the LangGraph
    # checkpointer holds the paused run — this column is only what lets
    # /api/chat/history show "there's a pending approval" after a page
    # reload, without querying the checkpointer directly. Cleared on resume.
    pending_interrupt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)
    __table_args__ = (Index("ix_conv_user_updated", "user_id", "updated_at"),
                      Index("ix_conv_tenant_intent", "tenant_id", "intent_level"))


class ChatMessage(Base):
    """One turn. `cited_product_ids` is what makes a chat answer auditable the
    same way a rec card is: the ids the answer was allowed to name, recorded
    next to the text that named them.
    """
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String)          # user | assistant
    content: Mapped[str] = mapped_column(Text)
    cited_product_ids: Mapped[dict] = mapped_column(JSON, default=list)
    model_used: Mapped[str] = mapped_column(String, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Which retrievers and which reranker produced this answer's candidates
    # ("hybrid+cross_encoder", "fts", …). Stored per message because it varies
    # per turn and is the first thing to look at when an answer cites the
    # wrong course — §3.1's degradation must be visible after the fact.
    retrieval_path: Mapped[str] = mapped_column(String, default="")
    # The mermaid learning path shipped with this turn, built in Python from
    # the cited courses (app/chat/pathway.py). Persisted so replaying a thread
    # renders the same diagram rather than rebuilding it against a catalog
    # that may have changed.
    pathway: Mapped[dict] = mapped_column(JSON, default=dict)
    intent_level: Mapped[str] = mapped_column(String, default="cold")
    intent_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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


class LLMCallLog(Base):
    """One row per actual provider call — chat agent and recommendation
    pipeline both write here. `AgentRun` records one row per recommendation
    *run* (retrieval rounds, node path); this is the finer grain underneath
    it and underneath every chat turn: which provider and model answered,
    how many tokens it cost, how long it took, and whether it was the
    primary provider or a fallback. That is what a token-utilization or
    cost dashboard needs and `AgentRun`/`ChatMessage` do not carry.

    Written for failed attempts too (status="error"), same reasoning as
    `AgentRun._record_run` — a table that only records successes cannot
    show which provider is actually flaky.
    """
    __tablename__ = "llm_call_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)             # fast | fast_fallback | writer | chat
    provider: Mapped[str] = mapped_column(String)          # mesh | groq | ollama
    model: Mapped[str] = mapped_column(String, default="")
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)  # provider != the primary tier
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="ok")  # ok | error
    error: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (Index("ix_llm_calls_provider_created", "provider", "created_at"),
                      Index("ix_llm_calls_kind_created", "kind", "created_at"))


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
