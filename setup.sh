#!/usr/bin/env bash
# ============================================================================
# SmartReco v2 — project bootstrap
# Scaffolds the full repo per SmartReco_Architecture_v2_FINAL.md, creates a
# venv, installs deps, initializes SQLite (WAL + FTS5 + indexes), and leaves
# you with a running FastAPI skeleton.
#
# The foundation files written here are WORKING code for the highest-risk
# pieces flagged in the critiques:
#   A1  WAL/pragma connect-listener        A2  structured-output fallback chain
#   A4  FTS5 table + sync triggers          A5  batched Mesh embeddings
#   A6  outbox indexes                      A7  dual-horizon interest scorer
#   A11 secure cookie flags                 A12 /api/events/stats
# Agent nodes / templates / tracker.js are stubbed with TODO markers that
# reference the architecture doc section to implement against.
#
# Usage:   bash setup.sh [target_dir]     (default: ./smartreco)
# After:   cd smartreco && source .venv/bin/activate
#          cp .env.example .env   # fill MESH_API_KEY
#          python -m app.db.init_db && python -m app.db.seed
#          uvicorn app.main:app --reload --workers 1
# ============================================================================
set -euo pipefail

TARGET="${1:-smartreco}"
PY="${PYTHON:-python3.11}"
command -v "$PY" >/dev/null 2>&1 || PY=python3

echo "==> Scaffolding SmartReco into ./$TARGET (python: $($PY --version))"
mkdir -p "$TARGET"; cd "$TARGET"

# ---------------------------------------------------------------------------
# Directory tree (arch §7, repo layout)
# ---------------------------------------------------------------------------
mkdir -p app/{db,auth,admin,catalog,tracking,agent/nodes,scheduler,web/templates,web/static} \
         app/admin/templates app/scheduler/templates \
         evals seed .github/workflows
touch app/__init__.py app/db/__init__.py app/auth/__init__.py app/admin/__init__.py \
      app/catalog/__init__.py app/tracking/__init__.py app/agent/__init__.py \
      app/agent/nodes/__init__.py app/scheduler/__init__.py app/web/__init__.py \
      evals/__init__.py

# ---------------------------------------------------------------------------
# requirements.txt — fastapi + openai pinned explicitly (CI critical check
# looks for a web framework AND an LLM client by name; arch §10 trap #6)
# ---------------------------------------------------------------------------
cat > requirements.txt << 'EOF'
fastapi>=0.115
uvicorn[standard]>=0.30
jinja2>=3.1
python-multipart>=0.0.9
itsdangerous>=2.2

sqlalchemy>=2.0
aiosqlite>=0.20

openai>=1.40
langchain-openai>=0.2
langgraph>=0.2
langgraph-checkpoint-sqlite>=2.0
langsmith>=0.1

chromadb>=0.5
apscheduler>=3.10
aiosmtplib>=3.0
httpx>=0.27

pydantic-settings>=2.4
passlib[bcrypt]>=1.7
EOF

# ---------------------------------------------------------------------------
# .gitignore / .env.example  (advisory CI checks: .env ignored, never committed)
# ---------------------------------------------------------------------------
cat > .gitignore << 'EOF'
.env
.venv/
__pycache__/
*.pyc
data/
chroma_data/
*.db
*.db-wal
*.db-shm
.langgraph_api/
EOF

cat > .env.example << 'EOF'
# --- Mesh API (mandatory: ALL LLM + embedding calls go through Mesh) ---
MESH_API_KEY=rsk_your_key_here
MESH_BASE_URL=https://api.meshapi.ai/v1
MODEL_FAST=google/gemini-2.5-flash
MODEL_FAST_FALLBACK=openai/gpt-4o-mini
MODEL_WRITER=openai/gpt-4o
EMBED_MODEL=openai/text-embedding-3-small

# --- App ---
SECRET_KEY=change_me_long_random
DATABASE_URL=sqlite+aiosqlite:///./data/smartreco.db
CHROMA_DIR=./chroma_data
ENV=development

# --- Agent policy (arch v2 §3, v1 §5.2) ---
RERANK_MODE=fusion              # fusion | cross_encoder | llm
FINGERPRINT_COS_THRESHOLD=0.15
TRIGGER_MIN_EVENTS=8
TRIGGER_DEBOUNCE_S=90
REC_STALE_HOURS=6
COLD_START_MIN_EVENTS=3

# --- Scheduler / digest (bonus tier — first cut) ---
SCHEDULER_ENABLED=true
DIGEST_HOUR=16
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=

# --- Observability (bonus) ---
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=smartreco
EOF

# ---------------------------------------------------------------------------
# app/config.py
# ---------------------------------------------------------------------------
cat > app/config.py << 'EOF'
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
EOF

# ---------------------------------------------------------------------------
# app/db/session.py — ▲A1: WAL + pragmas via connect-event listener.
# This is THE correct way with aiosqlite; setting it "once by hand" is the
# v1 trap. synchronous=NORMAL is safe under WAL.
# ---------------------------------------------------------------------------
cat > app/db/session.py << 'EOF'
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA cache_size=-64000")   # 64 MB
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


async_session = async_sessionmaker(engine, expire_on_commit=False)


async def wal_checkpoint():
    """Nightly job (▲A16): checkpoint + truncate the -wal file."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
EOF

# ---------------------------------------------------------------------------
# app/db/models.py — full v2 schema (arch v1 §2 + v2 §2 deltas)
# ---------------------------------------------------------------------------
cat > app/db/models.py << 'EOF'
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
EOF

# ---------------------------------------------------------------------------
# app/db/init_db.py — creates tables, FTS5 (▲A4) + sync triggers, indexes (▲A6)
# ---------------------------------------------------------------------------
cat > app/db/init_db.py << 'EOF'
"""python -m app.db.init_db — create schema, FTS5, triggers, indexes."""
import asyncio
import pathlib

from sqlalchemy import text

from app.db.models import Base
from app.db.session import engine

FTS_DDL = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
         title, description, category, tags,
         content=products, content_rowid=id)""",
    """CREATE TRIGGER IF NOT EXISTS products_fts_ai AFTER INSERT ON products BEGIN
         INSERT INTO products_fts(rowid, title, description, category, tags)
         VALUES (new.id, new.title, new.description, new.category, new.tags);
       END""",
    """CREATE TRIGGER IF NOT EXISTS products_fts_ad AFTER DELETE ON products BEGIN
         INSERT INTO products_fts(products_fts, rowid, title, description, category, tags)
         VALUES ('delete', old.id, old.title, old.description, old.category, old.tags);
       END""",
    """CREATE TRIGGER IF NOT EXISTS products_fts_au AFTER UPDATE ON products BEGIN
         INSERT INTO products_fts(products_fts, rowid, title, description, category, tags)
         VALUES ('delete', old.id, old.title, old.description, old.category, old.tags);
         INSERT INTO products_fts(rowid, title, description, category, tags)
         VALUES (new.id, new.title, new.description, new.category, new.tags);
       END""",
    # ▲A6 outbox indexes
    "CREATE INDEX IF NOT EXISTS idx_outbox_status_created ON vector_outbox(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_attempts ON vector_outbox(attempts) WHERE status='pending'",
]


async def main():
    pathlib.Path("data").mkdir(exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in FTS_DDL:
            await conn.execute(text(ddl))
    print("✓ schema + FTS5 + triggers + indexes created")


if __name__ == "__main__":
    asyncio.run(main())
EOF

# ---------------------------------------------------------------------------
# app/agent/mesh.py — ▲A2 structured fallback chain + ▲A5 batch embeddings.
# EVERY AI call in the system goes through this module → Mesh. (Mandate.)
# ---------------------------------------------------------------------------
cat > app/agent/mesh.py << 'EOF'
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

from openai import AsyncOpenAI
from sqlalchemy import select

from app.config import settings
from app.db.models import EmbeddingCache
from app.db.session import async_session

log = logging.getLogger("mesh")

client = AsyncOpenAI(base_url=settings.MESH_BASE_URL, api_key=settings.MESH_API_KEY)

RETRY_STATUS = {429, 500, 502, 503}


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


async def _chat(model: str, messages: list, **kw):
    delay = 1.0
    for attempt in range(4):
        try:
            return await client.chat.completions.create(model=model, messages=messages, **kw)
        except Exception as e:  # backoff on transient errors
            status = getattr(e, "status_code", None)
            if attempt == 3 or (status is not None and status not in RETRY_STATUS):
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def structured_call(messages: list, schema: dict, schema_name: str = "out") -> tuple[dict, str, bool]:
    """Returns (parsed, model_used, fallback_used)."""
    rf = {"type": "json_schema",
          "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
    try:
        resp = await _chat(settings.MODEL_FAST, messages, response_format=rf, temperature=0)
        return _parse_json(resp.choices[0].message.content), settings.MODEL_FAST, False
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as e:
        log.warning("structured parse failed on %s (%s) → fallback %s",
                    settings.MODEL_FAST, e, settings.MODEL_FAST_FALLBACK)
    resp = await _chat(settings.MODEL_FAST_FALLBACK, messages, response_format=rf, temperature=0)
    return _parse_json(resp.choices[0].message.content), settings.MODEL_FAST_FALLBACK, True


async def writer_call(messages: list, schema: dict) -> tuple[dict, str]:
    """Persuasive generation under a strict schema (arch v1 §5.4 generate)."""
    rf = {"type": "json_schema",
          "json_schema": {"name": "recommendation", "schema": schema, "strict": True}}
    resp = await _chat(settings.MODEL_WRITER, messages, response_format=rf, temperature=0.7)
    return _parse_json(resp.choices[0].message.content), settings.MODEL_WRITER


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """▲A5: one API call for the whole batch, with read-through cache."""
    hashes = [_h(t) for t in texts]
    out: dict[str, list[float]] = {}
    async with async_session() as s:
        rows = (await s.execute(
            select(EmbeddingCache).where(EmbeddingCache.text_hash.in_(hashes)))).scalars()
        for r in rows:
            out[r.text_hash] = r.vector
    missing = [(h, t) for h, t in zip(hashes, texts) if h not in out]
    if missing:
        resp = await client.embeddings.create(model=settings.EMBED_MODEL,
                                              input=[t for _, t in missing])
        async with async_session() as s:
            for (h, _), item in zip(missing, resp.data):
                out[h] = item.embedding
                s.add(EmbeddingCache(text_hash=h, vector=item.embedding))
            await s.commit()
    return [out[h] for h in hashes]


async def check_models_at_startup():
    """Log structured-output support per configured model. Don't assume."""
    try:
        models = await client.models.list()
        ids = {m.id for m in models.data}
        for m in (settings.MODEL_FAST, settings.MODEL_FAST_FALLBACK, settings.MODEL_WRITER):
            log.info("mesh model %s: %s", m, "available" if m in ids else "NOT LISTED — verify")
    except Exception as e:
        log.warning("could not list Mesh models at startup: %s", e)
EOF

# ---------------------------------------------------------------------------
# app/agent/scorer.py — ▲A7 dual-horizon deterministic scorer + fingerprint.
# Zero LLM calls. This file IS the efficiency thesis.
# ---------------------------------------------------------------------------
cat > app/agent/scorer.py << 'EOF'
"""Deterministic interest scorer — the cheap layer that gates the agent.

▲A7 dual horizon: λ_long = ln2/72h (enduring interests), λ_short = ln2/6h
(current session intent). merged = 0.6·long + 0.4·short. Fingerprint over
merged. ▲A8 feedback events reweight the same model — this is the feedback
loop, no extra ML.
"""
import hashlib
import math
from collections import defaultdict
from datetime import datetime, timezone

WEIGHTS = {
    "page_view": 1.0, "product_view": 2.0, "search": 3.0,
    "search_result_click": 3.0, "category_filter": 1.5, "scroll_depth_75": 1.0,
    "add_to_wishlist": 5.0, "cta_click": 6.0,
    "conversion": 10.0,     # ▲A8
    "rec_click": 4.0,       # ▲A8 positive feedback
    "rec_dismiss": -3.0,    # ▲A8 negative feedback
}
DWELL_BONUS = 2.0           # dwell > 30s
LAMBDA_LONG = math.log(2) / 72.0    # hours
LAMBDA_SHORT = math.log(2) / 6.0


def _score(events, lam, now):
    scores: dict[str, float] = defaultdict(float)
    for e in events:
        cat = e.get("category")
        if not cat:
            continue
        age_h = max(0.0, (now - e["ts"]).total_seconds() / 3600.0)
        w = WEIGHTS.get(e["event_type"], 0.0)
        if e["event_type"] == "product_dwell" and (e.get("dwell_ms") or 0) > 30_000:
            w += DWELL_BONUS
        if e["event_type"] == "scroll_depth" and (e.get("meta") or {}).get("depth", 0) >= 75:
            w += WEIGHTS["scroll_depth_75"]
        scores[cat] += w * math.exp(-lam * age_h)
    total = sum(v for v in scores.values() if v > 0) or 1.0
    return {k: max(0.0, v) / total for k, v in
            sorted(scores.items(), key=lambda kv: -kv[1])[:5]}


def score_interests(events, now=None):
    """events: [{event_type, category, ts, dwell_ms?, meta?}] → dict of vectors."""
    now = now or datetime.now(timezone.utc)
    long_v = _score(events, LAMBDA_LONG, now)
    short_v = _score(events, LAMBDA_SHORT, now)
    cats = set(long_v) | set(short_v)
    merged = {c: 0.6 * long_v.get(c, 0.0) + 0.4 * short_v.get(c, 0.0) for c in cats}
    tot = sum(merged.values()) or 1.0
    merged = {k: v / tot for k, v in sorted(merged.items(), key=lambda kv: -kv[1])[:5]}
    return {"long": long_v, "short": short_v, "merged": merged}


def fingerprint(merged: dict, price_band: str = "", stage: str = "") -> str:
    """Coarse on purpose: top-3 merged categories at 0.1 buckets + band + stage."""
    top3 = sorted(merged.items(), key=lambda kv: -kv[1])[:3]
    payload = "|".join(f"{c}:{round(s, 1)}" for c, s in top3) + f"|{price_band}|{stage}"
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def cosine_distance(a: dict, b: dict) -> float:
    cats = set(a) | set(b)
    if not cats:
        return 0.0
    va = [a.get(c, 0.0) for c in cats]
    vb = [b.get(c, 0.0) for c in cats]
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va)) or 1.0
    nb = math.sqrt(sum(x * x for x in vb)) or 1.0
    return 1.0 - dot / (na * nb)
EOF

# ---------------------------------------------------------------------------
# app/tracking/queue.py + routes.py — non-blocking ingest (arch v1 §4) + ▲A12
# ---------------------------------------------------------------------------
cat > app/tracking/queue.py << 'EOF'
"""asyncio queue + batch writer. Handler does validation + put — nothing else."""
import asyncio
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import Event
from app.db.session import async_session

log = logging.getLogger("tracking")

EVENT_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=10_000)
DROPPED = 0
FLUSH_ROWS = 200
FLUSH_SECONDS = 1.0


async def writer_loop():
    buf = []
    while True:
        try:
            item = await asyncio.wait_for(EVENT_QUEUE.get(), timeout=FLUSH_SECONDS)
            buf.extend(item)
        except asyncio.TimeoutError:
            pass
        if buf and (len(buf) >= FLUSH_ROWS or EVENT_QUEUE.empty()):
            rows, buf = buf, []
            try:
                async with async_session() as s:
                    stmt = sqlite_insert(Event).values(rows)
                    # idempotent on client event_uuid (retries are safe)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["event_uuid"])
                    await s.execute(stmt)
                    await s.commit()
            except Exception:
                log.exception("event batch insert failed (%d rows)", len(rows))


async def stats():
    async with async_session() as s:
        today = (await s.execute(
            select(func.count(Event.id)).where(func.date(Event.ts) == date.today().isoformat())
        )).scalar() or 0
    return {"queue_depth": EVENT_QUEUE.qsize(), "processed_today": today, "dropped": DROPPED}
EOF

cat > app/tracking/routes.py << 'EOF'
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.tracking import queue as tq

router = APIRouter()


class TrackedEvent(BaseModel):
    event_uuid: str
    event_type: str
    product_id: int | None = None
    query: str | None = None
    dwell_ms: int | None = None
    meta: dict = Field(default_factory=dict)
    ts: str  # ISO from client; server re-parses defensively


class EventBatch(BaseModel):
    events: list[TrackedEvent]


@router.post("/api/events", status_code=202)
async def ingest(batch: EventBatch, request: Request):
    sid = request.cookies.get("sid", "anon")
    user_id = getattr(request.state, "user_id", None)
    rows = [{**e.model_dump(), "session_id": sid, "user_id": user_id} for e in batch.events]
    batch_id = str(uuid.uuid4())
    try:
        tq.EVENT_QUEUE.put_nowait(rows)
    except Exception:
        tq.DROPPED += len(rows)
    return {"accepted": len(rows), "batch_id": batch_id}


@router.get("/api/events/stats")  # ▲A12
async def event_stats():
    return await tq.stats()
EOF

# ---------------------------------------------------------------------------
# app/main.py — lifespan wires queue writer + optional scheduler
# ---------------------------------------------------------------------------
cat > app/main.py << 'EOF'
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.mesh import check_models_at_startup
from app.config import settings
from app.tracking.queue import writer_loop
from app.tracking.routes import router as tracking_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    writer = asyncio.create_task(writer_loop())
    scheduler = None
    if settings.SCHEDULER_ENABLED:
        from app.scheduler.jobs import build_scheduler  # noqa: WPS433
        scheduler = build_scheduler()
        scheduler.start()
    if settings.use_mesh:
        asyncio.create_task(check_models_at_startup())
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    writer.cancel()


app = FastAPI(title="SmartReco", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.include_router(tracking_router)

# TODO wire as they're built (arch v2 §8 schedule):
# from app.auth.routes import router as auth_router        # Aug 5
# from app.admin.routes import router as admin_router      # Aug 5
# from app.catalog.routes import router as catalog_router  # Aug 5
# from app.web.routes import router as web_router          # Aug 6


@app.get("/healthz")
@app.get("/health")   # ▲B9 alias — deploy platforms probe either
async def healthz():
    return {"ok": True, "env": settings.ENV}
EOF

# ---------------------------------------------------------------------------
# app/scheduler/jobs.py — skeleton (digest is bonus tier, first cut)
# ---------------------------------------------------------------------------
cat > app/scheduler/jobs.py << 'EOF'
"""APScheduler jobs. SINGLE WORKER ONLY (uvicorn --workers 1) — arch trap #3."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db.session import wal_checkpoint


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="Asia/Kolkata")
    # 30s outbox drain — TODO app.catalog.outbox.drain_outbox (batch embeds ▲A5)
    #   ▲B7: embed in chunks of 20; failed chunk → retry per-item; attempts+backoff
    #   absorb stragglers. Chroma upsert takes lists — one call per chunk.
    # 15m stale-rec refresh for active users — TODO
    sched.add_job(nightly_maintenance, "cron", hour=3, id="nightly")
    # 16:00 digest — BONUS. Ship POST /admin/trigger-digest first; cron only
    # if everything else is green by Aug 8 PM (arch v2 §8).
    return sched


async def nightly_maintenance():
    """▲A16: WAL checkpoint + orphan event cleanup + SQL↔Chroma reconcile."""
    await wal_checkpoint()
    # TODO: DELETE events WHERE user_id IS NULL AND ts < now-90d
    # TODO: reconcile products.content_hash vs Chroma metadata
EOF

# ---------------------------------------------------------------------------
# Agent node stubs — implement Aug 7 against arch v2 §3
# ---------------------------------------------------------------------------
for n in analyze retrieve fusion_rank grade generate validate; do
cat > "app/agent/nodes/${n}.py" << EOF
"""${n} node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.
${n} notes:
$( case $n in
  analyze)     echo "  LLM #1 via mesh.structured_call (fallback chain built in).
  Input includes BOTH horizons from scorer: interests (long) + interests_short.
  Output: {intent, categories[], level, budget_band, stage, retrieval_queries[<=3]}";;
  retrieve)    echo "  Per-interest Chroma query + FTS5 MATCH query, merge with RRF (k=60).
  Metadata filter: is_active, price ceiling, level within one step.";;
  fusion_rank) echo "  ▲A14+B2 deterministic: 0.45*norm(rrf) + 0.3*interest_match
  + 0.1*rating_prior + 0.1*popularity(log view-count from events GROUP BY)
  + 0.05*graph_adjacency(related/prereq of viewed). Then:
  ▲B4 EXCLUDE products with a conversion event for this user;
      -0.15 penalty for items in the user's previous current rec.
  ▲B3 diversity cap: <=3 of final 5 from one category. Top 8 to grade.
  RERANK_MODE flag can swap to cross_encoder or llm.";;
  grade)       echo "  ▲A3 deterministic coverage FIRST (top-5 cover inferred cats?).
  ratio==1 → generate. ratio==0 → refine (1 loop max). else → llm grade (rare).";;
  generate)    echo "  LLM #2 via mesh.writer_call. Narrative must reference both horizons.
  Per item: reason + hook. Confidence attached AFTER in Python (▲A9) — never
  asked of the LLM.";;
  validate)    echo "  Pure Python. IDs ⊆ retrieved set, active, <=5, deduped, reason<=220ch.
  One regeneration on violation, then drop offenders. The grounding guarantee.";;
esac )
"""
EOF
done

cat > app/agent/graph.py << 'EOF'
"""LangGraph assembly — arch v2 §3. Build Aug 7.
State TypedDict: user_id, events, interests, interests_short, profile, queries,
candidates, ranked, grade, draft, retries, fallback_used.
Checkpoint with SqliteSaver. Trace to LangSmith when LANGSMITH_TRACING=true.
"""
EOF

cat > app/agent/triggers.py << 'EOF'
"""Trigger policy — arch v1 §5.2, unchanged in v2. THIS IS THE PLANNER.
Run agent iff: cosine(old,new) > threshold | >=N significant events |
rec stale + user active | high-intent event. Suppress: 90s debounce,
per-user asyncio.Lock + DB running flag, cold-start floor (<3 events →
trending-within-observed-signal, never generic popular).
"""
EOF

# ---------------------------------------------------------------------------
# Web stubs
# ---------------------------------------------------------------------------
cat > app/web/static/tracker.js << 'EOF'
/* tracker.js — implement per arch v1 §4 + v2 A13/A8. ~120 lines, no deps.
   Batch: 10 events | 5s | sendBeacon on visibilitychange→hidden.
   sendBeacon ignores headers — session rides the cookie (trap #5).
   Scroll (▲A13): check every 150ms, emit HIGHEST milestone crossed.
   New events (▲A8): rec_click, rec_dismiss, conversion.
   Idempotency: crypto.randomUUID() per event. Queue cap 200. */
EOF

cat > app/web/templates/base.html << 'EOF'
<!doctype html><html><head><meta charset="utf-8"><title>SmartReco</title></head>
<body>{% block content %}{% endblock %}
<script src="/static/tracker.js" defer></script></body></html>
EOF

# ---------------------------------------------------------------------------
# Seed placeholder + seeder using batched embeddings
# ---------------------------------------------------------------------------
cat > seed/products.json << 'EOF'
[
  {
    "title": "Agentic AI Bootcamp", "slug": "agentic-ai-bootcamp",
    "description": "REPLACE: generate ~60 courses across 8 categories (Agentic AI, LLM Engineering, Data Engineering, MLOps, Computer Vision, NLP, Analytics, Cloud), 3 levels, 3 price bands. Spend 30-45 min making descriptions genuinely diverse (Grok #6) — generic blurbs make retrieval look weak. Include prereq_ids/related_ids (▲A10).",
    "category": "Agentic AI", "level": "intermediate", "price": 2999,
    "tags": ["langgraph", "agents"], "prereq_ids": [], "related_ids": [],
    "instructor": "TBD", "rating": 4.8
  }
]
EOF

cat > app/db/seed.py << 'EOF'
"""python -m app.db.seed — load seed/products.json into SQL + Chroma.
Uses mesh.embed_batch (▲A5): 60 products ≈ 1-2 embedding API calls, not 60.
Day-1 simplification (arch v2 §8): synchronous dual-write here; outbox
upgrade lands Aug 8 AM if the core loop is green.
"""
import asyncio
import hashlib
import json
import pathlib

import chromadb

from app.agent.mesh import embed_batch
from app.config import settings
from app.db.models import Product
from app.db.session import async_session


def embedding_text(p: dict) -> str:
    return (f"{p['title']}. Category: {p['category']}. Level: {p['level']}. "
            f"Tags: {', '.join(p.get('tags', []))}. {p['description']}")


def validate_graph(products: list[dict]):
    """▲B6: every prereq/related slug must exist. Fail loudly, before any writes."""
    slugs = {p["slug"] for p in products}
    bad = [(p["slug"], ref) for p in products
           for ref in (p.get("prereq_ids", []) + p.get("related_ids", []))
           if ref not in slugs]
    if bad:
        raise SystemExit(f"seed graph references unknown slugs: {bad}")


async def main():
    products = json.loads(pathlib.Path("seed/products.json").read_text())
    validate_graph(products)
    texts = [embedding_text(p) for p in products]
    vectors = await embed_batch(texts) if settings.use_mesh else None

    client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
    coll = client.get_or_create_collection("products")  # NEVER default embed fn (trap #1)

    async with async_session() as s:
        objs = []
        for p, t in zip(products, texts):
            obj = Product(**{k: v for k, v in p.items()}, content_hash=hashlib.sha256(t.encode()).hexdigest())
            s.add(obj)
            objs.append(obj)
        await s.commit()
        for o in objs:
            await s.refresh(o)

    if vectors:
        coll.upsert(
            ids=[str(o.id) for o in objs],
            embeddings=vectors,
            metadatas=[{"category": p["category"], "level": p["level"],
                        "price": p["price"], "rating": p.get("rating", 4.5),
                        "is_active": True, "content_hash": o.content_hash}
                       for p, o in zip(products, objs)],
            documents=texts,
        )
        print(f"✓ seeded {len(objs)} products → SQL + Chroma ({len(vectors)} vectors, batched)")
    else:
        print(f"✓ seeded {len(objs)} products → SQL only (no MESH_API_KEY — Chroma skipped)")


if __name__ == "__main__":
    asyncio.run(main())
EOF

# ---------------------------------------------------------------------------
# README stub following the graded outline (arch v1 §11)
# ---------------------------------------------------------------------------
cat > README.md << 'EOF'
# SmartReco — Behavioral AI Recommendation Agent

> Skeleton README — fill Aug 8 PM per arch v1 §11 outline. Section 3 leads.

1. What it is + 60s demo GIF
2. Architecture diagram
3. **How we avoid wasteful LLM calls** — deterministic dual-horizon scorer,
   fingerprint gating, trigger policy, three caches. MEASURED numbers here.
4. **How recommendations are grounded** — dual-write, hybrid retrieval, validate node
5. Agent workflow (2-LLM happy path) + LangSmith trace screenshot
6. Event tracking design + p95 number
7. Bonus features implemented — name all four
8. Evaluation results + ablation (single-run, labeled directional)
9. Setup & run (clean-clone verified)
10. Trade-offs & future work (planner-LLM analysis, knowledge graph, learned
    ranker, Redis Streams, collaborative filtering, A/B over Mesh fan-out)
EOF

# ---------------------------------------------------------------------------
# Eval harness stub with the 7 personas
# ---------------------------------------------------------------------------
cat > evals/personas.py << 'EOF'
"""7 personas (v1's four + DeepSeek's three, ▲A15). Replay as event streams,
then measure: grounding rate (target 100%), category precision@5 (>0.8),
LLM calls/100 events (<6), cache hit rate (>60%), latencies,
plus ▲B8: Coverage (% of catalog ever recommended across all personas)
and Diversity@5 (mean distinct categories per rec set).
Every README number: measured or labeled — never invented.
two_interests directly validates per-interest multi-query + RRF —
a blended-query design fails it.
"""
PERSONAS = {
    "career_switcher": {}, "advanced_practitioner": {},
    "budget_beginner": {}, "no_intent_browser": {},
    "confused": {},          # mixed searches, consistent dwell → infer true interest
    "price_sensitive": {},   # expects low price_band recs
    "two_interests": {},     # expects BOTH categories represented
}
EOF

# ---------------------------------------------------------------------------
# CI workflow reminder (platform-supplied file — do not hand-edit)
# ---------------------------------------------------------------------------
cat > .github/workflows/README.txt << 'EOF'
Place the RE-DOWNLOADED platform workflow here as smartreco-checks.yml:
  https://careerapi-production.krishnaik.in/api/ci/hackathons/smartreco-build-challenge-2026/workflow.yml
Add repo secrets: MESH_API_KEY (use a THROWAWAY spend-capped key — the
workflow downloads and executes remote code with your secrets in env),
SUBMISSION_TOKEN. Revoke the key after the event.
EOF

# ---------------------------------------------------------------------------
# ▲B9 dev-ex: Makefile + smoke tests + ruff (Docker/Alembic/pre-commit
# deliberately omitted — see arch v2.1 C7)
# ---------------------------------------------------------------------------
cat > requirements-dev.txt << 'EOF'
-r requirements.txt
pytest>=8.0
pytest-asyncio>=0.23
ruff>=0.6
EOF

cat > Makefile << 'EOF'
.PHONY: dev seed test lint init

init:
	python -m app.db.init_db

seed:
	python -m app.db.seed

dev:
	uvicorn app.main:app --reload --workers 1

test:
	python -m pytest tests/ -q

lint:
	ruff check app/ evals/ tests/
EOF

mkdir -p tests && touch tests/__init__.py
cat > tests/test_smoke.py << 'EOF'
"""Smoke tests — /healthz and non-blocking event ingest. Run: make test.
ENV=test in conftest keeps Mesh out of the loop (settings.use_mesh False)."""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_healthz():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.asyncio
async def test_event_ingest_202():
    payload = {"events": [{
        "event_uuid": str(uuid.uuid4()), "event_type": "product_view",
        "product_id": 1, "meta": {}, "ts": "2026-08-05T12:00:00Z"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/events", json=payload)
    assert r.status_code == 202 and r.json()["accepted"] == 1
EOF

cat > tests/conftest.py << 'EOF'
import os

os.environ.setdefault("ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
EOF

cat > ruff.toml << 'EOF'
line-length = 100
[lint]
select = ["E", "F", "I", "W"]
EOF

# ---------------------------------------------------------------------------
# venv + install + init
# ---------------------------------------------------------------------------
echo "==> Creating venv and installing dependencies"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "==> Initializing database (schema + FTS5 + triggers + indexes)"
python -m app.db.init_db

deactivate

cat << 'DONE'

============================================================================
✓ SmartReco v2 scaffolded.

NEXT (in order):
  1. cd into the project, `source .venv/bin/activate`
  2. cp .env.example .env  →  set MESH_API_KEY (throwaway, spend-capped)
  3. Replace seed/products.json with ~60 real courses (30-45 focused minutes
     — retrieval quality depends on it), incl. prereq_ids/related_ids
  4. python -m app.db.seed        # batched embeddings → SQL + Chroma
  5. uvicorn app.main:app --reload --workers 1     # SINGLE worker, always
  6. curl localhost:8000/healthz && curl localhost:8000/api/events/stats
  7. Drop the re-downloaded platform workflow into .github/workflows/,
     add both secrets, push → CI green tonight.

Then build against SmartReco_Architecture_v2_FINAL.md §8 (day-by-day).
Working foundations already in place: pragmas listener, full schema, FTS5,
Mesh client w/ fallback + batch embeds, dual-horizon scorer, event queue.
Stubs marked TODO reference their arch section.
============================================================================
DONE
