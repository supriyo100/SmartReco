# SmartReco — Architecture (FINAL)

**Status:** approved for build. Rewritten 6 Aug 2026 against the code that exists, superseding the
v2.1 adjudication document (which was written before the catalog pipeline landed and no longer
describes the system).
**Deadline:** 9 Aug 2026 12:00 IST.
**Stack:** FastAPI · SQLite (WAL + FTS5) · Chroma · LangGraph · APScheduler · Jinja2 · Mesh API ·
LangSmith (optional).

---

## 0. What this system is

A course-recommendation platform that watches what a user actually does — pages browsed, searches
run, time spent, cards clicked and dismissed — and turns that behavior into a small set of grounded,
persuasive recommendations.

The thesis in one sentence: **the expensive part (an LLM) runs only when cheap deterministic code
has established that behavior materially changed.** Everything below is downstream of that.

Three claims the build must be able to demonstrate:

1. **Behavior drives recommendations.** Not a static popularity list with an LLM writing captions.
2. **Recommendations are grounded.** Every recommended course ID came from retrieval over the real
   catalog, and a validate node enforces that — the model cannot invent a course.
3. **The system is efficient by design.** A deterministic planner gates LLM calls; the README
   reports measured call counts, not adjectives.

---

## 1. The platform (foundation)

This is the layer the rest of the system sits on, and the part that must be finished first.

**Status: built and verified** (6 Aug 2026). Auth, roles, catalog browsing, admin CRUD, user
profiles and the identity/session middleware are implemented and exercised end to end — 45
assertions covering the anonymous → register → stitch → login → promote → CRUD → logout path, plus
the real 12-course catalog rendering, searching and resolving its ladder links, plus 60 tests in
`tests/` and a 16-assertion live-server journey. Modules: `app/auth/`, `app/admin/`,
`app/profiles/`, `app/catalog/routes.py`, `app/web/`, wired in `app/main.py`.

**Schema creation caveat.** `python -m app.db.init_db` now reconciles *added columns* on existing
tables and reports each one. It previously called only `create_all()`, which creates missing tables
but never alters an existing one — so adding a model field and re-running printed a success line and
applied nothing, deferring the failure to a runtime `no such column`. Still not a migration tool:
renames, drops and type changes are undetected.

### 1.1 Web application

A server-rendered FastAPI app (Jinja2 templates, no SPA). Server-rendered is a deliberate choice:
the behavioral tracker needs real page loads to observe, and a four-day window has no room for a
frontend build pipeline.

**Authentication — email/password, deliberately simple.** No OAuth, no magic links, no email
verification. Passwords hashed with **bcrypt directly, not `passlib`** — passlib 1.7.4 is
unmaintained and its bcrypt backend breaks against bcrypt ≥ 4.1 (it reads `bcrypt.__about__`, which
no longer exists, then hands `hashpw` an over-length config that bcrypt 5 rejects). The two calls we
need are `hashpw`/`checkpw`. Passwords over bcrypt's 72-byte limit are **rejected, not truncated**.

Session held in a signed cookie (`itsdangerous`), flags `HttpOnly` + `SameSite=lax`. `Secure` is set
**from the request scheme, not from `ENV`**: keying it off the env name means any non-development
deployment served over plain http sets a cookie the browser then refuses to send back, so login
appears to succeed and every later request is silently anonymous. `request.url.scheme` already
reflects `X-Forwarded-Proto` under `uvicorn --proxy-headers`, the usual TLS-proxy setup.

The cookie carries `user_id` and `role`, but **`role` is re-read from the DB on each authenticated
request** (`identity_middleware`) rather than trusted from the cookie — a cookie signed before a
promotion or demotion would otherwise carry the stale role for up to 14 days. One indexed PK lookup
is the right price. A user deleted since signing is treated as anonymous. There is still no
server-side session store, because there is nothing in a session worth the table.

Registration always creates `role='user'`; the first admin comes from
`python -m app.auth.cli create-admin <email>` (or `make-admin` to promote an existing user).

**Two roles, one column.** `users.role ∈ {user, admin}`:

| Role | Can do |
|---|---|
| `user` | Browse and search the catalog, view course pages, receive recommendations, click/dismiss recommendation cards. |
| `admin` | Everything a user can, plus full product CRUD, the ingest/reingest trigger, and the `agent_runs` observability page. |

Enforcement is a single FastAPI dependency, `require_admin`, applied to the admin router — not
per-handler checks, which is how one handler eventually gets missed. Anonymous visitors may browse
and are tracked by `session_id`; on login their prior anonymous events are stitched to their
`user_id` (§4.2), so a first recommendation can draw on what they did before signing up.

**Route map**

```
GET  /                     landing / browse
GET  /search?q=            catalog search (FTS5 + filters)
GET  /course/{slug}        course detail — the main tracked surface
GET  /recommendations      the rec cards (§6)

GET  POST /auth/register   email + password
GET  POST /auth/login
POST /auth/logout

POST /api/events           tracker ingest → 202 (§4)
GET  /api/events/stats     queue depth, processed, dropped

GET  /admin/products                 list
GET  POST /admin/products/new        create
GET  POST /admin/products/{id}/edit  update
POST /admin/products/{id}/delete     soft delete (is_active=false)
POST /admin/ingest                   re-run catalog ingest
GET  /admin/agent-runs               observability

GET  /healthz  /health     liveness
```

### 1.2 Database schema

SQLite with WAL (`app/db/session.py` sets the pragmas on every connect: `journal_mode=WAL`,
`synchronous=NORMAL`, 64 MB cache, `temp_store=MEMORY`, `foreign_keys=ON`). One writer, many
readers — which is exactly this workload.

The tables and how they relate:

```
users ──1:1──► user_profiles          derived interest state, one row per user
  │
  ├──1:N──► events                    the raw behavioral log (user_id nullable)
  │
  └──1:N──► recommendations           stored rec sets, one current per user

products ──1:N──► vector_outbox       pending Chroma sync work
   │
   └── products_fts                   FTS5 virtual table, trigger-maintained

agent_runs        one row per agent invocation (observability)
embedding_cache   text_hash → vector, avoids re-embedding identical text
digest_log        (user_id, sent_date) unique — digest idempotency
```

**`users`** — `id`, `email` (unique), `password_hash`, `role`, `digest_opt_in`, `created_at`.

**`products`** — the course catalog. `id`, `slug` (unique, the stable identity), `title`,
`description`, `category`, `level`, `price`, `tags`, `instructor`, `rating`, `is_active`,
`content_hash`, plus the two ladder columns `prereq_ids` / `related_ids` (§3.3), and timestamps.
`content_hash` is what makes re-ingest cheap: unchanged courses are skipped rather than re-embedded.

**`events`** — the behavioral log and the source of truth for everything the recommender knows.
`event_uuid` is unique and client-generated, which makes retries idempotent — the tracker uses
`sendBeacon`, which can duplicate on page unload. `user_id` is nullable (anonymous browsing);
`session_id` is always present. Indexed on `(user_id, ts)` and `(session_id, ts)`, because every
read is "recent events for this identity."

Event types: `page_view`, `product_view`, `product_dwell`, `search`, `search_result_click`,
`category_filter`, `add_to_wishlist`, `cta_click`, `scroll_depth`, `conversion`, `rec_click`,
`rec_dismiss`.

**`user_profiles`** — two kinds of signal about a person, deliberately in one row but never mixed.

*Derived* (written by the interest model, §5): `interests` (long horizon) and `interests_short`
(session horizon) as category→weight JSON, `price_band`, `stage`, `llm_summary`, `fingerprint`,
`events_seen`. Never authoritative; fully rebuildable from `events`.

*Declared* (written by the user at `/profile`): `full_name`, `headline`, `bio`, `goals`, `skills`,
`experience_years`, `resume_filename` / `resume_path` / `resume_text` / `resume_uploaded_at`.

They are separated because they age and are trusted differently — behavior is current but narrow, a
resume is broad but stale the day after it is written — and because their durability requirements
are **opposite**: the derived half can be recomputed and the declared half cannot. Any future
"recompute profiles" job must rewrite only the derived columns. The profile router touches only the
declared ones, so it can never corrupt the interest model.

The declared half exists to answer cold start: a brand-new account has no behavior, so a stated
background is the only signal available at t=0. The resume's extracted **text** is stored alongside
the file, because text is what the generate node can use; re-parsing on every read would be absurd.
Extraction that fails does so **loudly** (the upload succeeds, the file is kept, and the page says
text could not be extracted) — an extractor returning `""` silently would leave a user with a green
checkmark and an empty profile.

**`recommendations`** — `narrative` plus an `items` JSON array of
`{product_id, reason, hook, confidence, next_step_id, rank}`, the `fingerprint` it was generated
for, `trigger_reason`, `model_used`, `token_cost`, and `is_current`. Indexed `(user_id, is_current)`.
Old sets are kept with `is_current=false` — they are the recency-penalty input (§5.3) and the
audit trail.

**`vector_outbox`** — `product_id`, `op`, `status`, `attempts`, `last_error`. The transactional
sync record between SQLite and Chroma (§3.4). Indexed `(status, created_at)` plus a partial index
on `attempts`.

**`agent_runs`** — one row per invocation: `node_path`, `llm_calls`, `retrieval_rounds`,
`fallback_used`, `cos_dist`, `trigger_reason`, `cache_hit`, `latency_ms`, `status`. This table is
where the README's efficiency numbers come from; without it they would be assertions.

Schema, FTS5 table, triggers and indexes are all created by `python -m app.db.init_db`. There is no
migration tool — `init_db` is the migration story for a greenfield app with a four-day life.

---

## 2. The catalog — hand-curated, three-tier

The catalog is not scraped. Each course is hand-written into `data/data_1/<slug>.json` from its
source page, against a frozen standard (`data/COURSE_SCHEMA.md`) enforced by
`data/course.schema.json` and `data/data_1/validate_seed.py`. Twelve courses are curated today;
`data/courses_catalogue.json` is the generated tracker (`make catalogue`) that reports counts,
distributions and open issues.

This matters architecturally because of §0 — a curated catalog is what makes "grounded" checkable.
Marketing pages lie by omission, and the standard exists to catch that: the validator has already
caught an objectives-vs-syllabus mismatch, a coupon price that would have gone stale in days, and a
27-vs-32 module version skew.

**The three tiers decide where a field is allowed to travel:**

| Tier | Destination | Fields |
|---|---|---|
| **T1 — Filter** | SQL columns + Chroma metadata | `slug` `category` `level` `price` `price_band` `is_free` `is_active` `mode` `enrollment_status` |
| **T2 — Embed** | Chroma chunk text | `title` `overview` `objectives` `module_groups` `projects` `skills` |
| **T3 — Persuade** | Injected into the generate prompt at answer time, **never embedded** | `price` `format` `mentors` `perks` `cohort_start` `career_roles` `rating` |

**The hard rule: a T3 field never enters embedding text.** Embedding "₹12000, Sat–Sun 8pm" makes
every course match every price query and every schedule query, and retrieval precision quietly
dies. `NEVER_EMBED_KEYS` in the validator enforces it.

Two more curation rules with architectural consequences:

- **The corroboration rule.** An objective is kept only if a real module supports it; uncorroborated
  ones go to `objectives_dropped` and are not embedded. The validate node cites objectives back to
  the user as reasons — an uncorroborated objective would be a lie with a citation attached.
- **`_comment_*` keys are the audit trail.** They live in the files forever, and `strip_comments()`
  in `app/catalog/loader.py` removes them before anything is written or embedded. `null` plus a
  comment beats a plausible guess, every time.

**Chunking** (`app/catalog/chunker.py`): one chunk per objective (the highest-value retrieval keys,
because objectives are phrased the way users phrase intent), one per *module group* — never per
module, since three-word titles embed terribly and 28 chunks from one course would dominate RRF and
starve a 12-course catalog — plus overview, projects and skills chunks. Every chunk carries
`parent_id`; retrieval dedupes to parent.

---

## 3. Retrieval and ranking

### 3.1 Hybrid retrieval

Two retrievers over the same catalog, fused by Reciprocal Rank Fusion:

- **Vector** — Chroma, embeddings via Mesh (`EMBED_MODEL`), one query per inferred interest rather
  than one blended query. Blending two interests produces a centroid that matches neither, which is
  exactly what the `two_interests` eval persona is built to catch.
- **FTS5** — the SQLite virtual table, trigger-maintained. Catches exact title, category and
  technology-name matches that semantic search softens.

RRF because it needs no score calibration between two retrievers whose scores are not comparable.

### 3.2 Deterministic fusion ranking

Default `RERANK_MODE=fusion`. A local cross-encoder stays available behind the flag but is off the
critical path — it would add ~2 GB of torch dependencies, a cold-start model download, and a
"why isn't this going through Mesh" question, to reorder a handful of candidates.

```
score = 0.40·norm(rrf)
      + 0.28·interest_match
      + 0.10·freshness
      + 0.09·popularity
      + 0.08·rating_prior
      + 0.05·graph_adjacency
```

Then two adjustments: a **−0.15 recency penalty** on items shown in the user's previous current rec
set (a penalty, not a hard exclusion — a 12-course catalog starves under exclusions), and **outright
exclusion** of anything with a `conversion` event, because you do not re-sell a course someone
bought. Final selection caps any single category at **3 of 5**, which protects multi-interest users
without the tuning burden of MMR.

### 3.3 Freshness — the live-vs-recorded axis

`app/catalog/freshness.py`. People want live and they want recent, and no amount of embedding
similarity captures that: a cohort starting in four weeks and the same syllabus recorded eighteen
months ago have nearly identical text. It is a fact about time, so it is computed, never asked of
an LLM.

```
freshness = enrollment_multiplier × (0.55·mode_prior + 0.45·recency)
```

Mode priors: `live` 1.00, `hybrid` 0.85, `self-paced` 0.60, `recorded` 0.45. Enrollment multiplier:
`open`/`closing_soon` 1.0, `waitlist` 0.7, `closed` 0.25.

**The demotion rule** is the load-bearing part: a `live` or `hybrid` course with **no future
cohort** is scored at the `recorded` prior. The course really is sold as live, but what a buyer gets
*today* is recordings of a cohort that already ran. Without this, a dead cohort outranks a genuinely
upcoming one on the strength of the word "live" in its metadata. `declared_mode` is preserved so the
card can still say "live bootcamp" truthfully.

Recency is measured from whichever date is meaningful for that mode: days-until-start for future
cohorts (0–60 days → 1.0), a 365-day half-life on `content_updated` otherwise, and **0.35 when no
date exists at all** — deliberately below the one-year mark, because unknown recency must never
outrank known-fresh and we do not invent a date to fill the gap.

### 3.4 The ladder, and dual-write

`prereq_ids` / `related_ids` are arrays of slugs — two ID lists, not a knowledge graph. They drive
the card's **Next step** line and the `0.05` adjacency boost. `validate_graph()` fails loudly on a
dangling edge, with a `prune_pending` escape hatch for incremental curation (edges to not-yet-written
courses drop in memory only, and reconnect themselves as files land).

**Dual-write — status: built and verified** (6 Aug 2026). `app/catalog/vectors.py` (Chroma write
path) + `app/catalog/outbox.py` (drain) + a 30 s scheduler job, with `/admin/sync` for visibility.
20 tests.

The shipped form is **fully asynchronous**, and the earlier note here — that the outbox is "about
failure recovery, not about correctness on the happy path" — was wrong, so it is corrected rather
than quietly dropped. Two stores cannot be committed atomically. A synchronous embed-on-upsert that
crashes between the SQLite commit and the Chroma call leaves them diverged **with no record that the
write was owed**, and nothing later can detect it. Writing the product row and the `vector_outbox`
row in one transaction is what makes the intent to sync as durable as the product. That is a
correctness property, not a recovery convenience.

Delivery is at-least-once, made safe by idempotent upserts. Four properties matter:

- **Last-op collapse** — five edits between drains are one embed of current state, not five.
- **`content_hash` skip** — a hash of the *embedded* text, so editing a non-embedded field (price)
  costs zero API calls.
- **Fatal vs transient classification** — 402/401/403/404 halt the drain, leave rows pending
  **without charging an attempt**, and surface an actionable message; 429/5xx fall back to per-item
  retry so one poison row cannot block the queue. (The first implementation retried per item on
  *any* failure, which turned a single 402 into 66 charged calls — see design.md §7.3.)
- **Deletes by `parent_id`, not by chunk id** — so deactivating a curated course removes all of its
  chunks, not just the one an admin-form delete would know about.

Two write paths share the collection: `ingest.py` writes many chunks per curated course; admin-form
products have no JSON behind them and get one chunk (`product::{id}::0`). Same metadata keys, so
retrieval never needs to know which produced a row.

---

## 4. Behavioral tracking

**Status: built and verified** (6 Aug 2026). Both halves — `app/web/static/tracker.js` and the
server ingest path. 24 behavior assertions run the tracker under Node against a DOM stub; a further
round-trip posts the exact JSON it emits to a live server and reads the rows back out of SQLite.

### 4.1 Non-blocking ingest

`tracker.js` batches events (10 events, or 5 s, or `sendBeacon` on unload) to `POST /api/events`,
which validates and pushes to an in-process `asyncio.Queue` (`maxsize=10_000`) and returns **202
immediately**. A background writer task drains in batches. Tracking must never make the site feel
slow — that is the whole design constraint. Measured: a page load performs zero network calls.

Queue overflow uses `put_nowait` with a `DROPPED` counter surfaced at `/api/events/stats` alongside
depth and processed-today. Dropping loudly beats blocking silently. The queue is dev-grade by
design; Redis Streams is the multi-instance answer and is named as future work rather than built.

Scroll milestones are checked every 150 ms and emit the **highest** milestone crossed, not the first
match on a throttled callback — a coarser throttle skips 50% milestones on fast scrolls. The
listener is registered `{passive: true}` so it can never block scrolling. Measured: 50 raw scroll
events produce 0 tracked events; a 0→100% scroll produces exactly 1.

### 4.1a Delivery under failure

Three failure modes are handled rather than assumed away:

- **Tab closes mid-batch.** `visibilitychange → hidden` *and* `pagehide` both flush via
  `sendBeacon` — neither alone suffices (`unload` doesn't fire when mobile backgrounds an app;
  `visibilitychange` can be skipped on bfcache navigation). This is why the session rides the `sid`
  **cookie**: `sendBeacon` cannot set headers (trap #5), so a header-based scheme would lose exactly
  the unload events that matter most.
- **Network down.** A rejected `fetch` or refused beacon puts the batch **back** on the queue,
  bounded by a 200-event cap — degrading to dropping the oldest rather than growing without limit.
- **Duplicates.** Every event carries `crypto.randomUUID()`; the writer uses
  `ON CONFLICT (event_uuid) DO NOTHING`, so retries and beacon duplicates cannot double-count.

**Dwell is visible-time, not wall-clock.** The timer pauses on hide and resumes on show — a tab left
open overnight is not thirty thousand seconds of interest. Under 1 s is a bounce and is not
recorded; over 30 min is discarded as a stuck timer.

The entire file is a try/catch-wrapped IIFE with fire-and-forget sends: an analytics bug must not
take the product down with it.

### 4.2 Identity stitching

Anonymous events carry `session_id` only. On login or registration, events for that `session_id`
with `user_id IS NULL` are backfilled with the new `user_id`. This is what lets a brand-new account
receive a behavior-driven first recommendation instead of a cold-start placeholder.

---

## 5. The interest model — deterministic, dual-horizon

`app/agent/scorer.py`. Pure Python, no model, and it is the layer that makes the whole efficiency
argument work.

Every event has a weight, decayed exponentially by age and summed per category:

```
page_view 1.0 · product_view 2.0 · search 3.0 · search_result_click 3.0
category_filter 1.5 · scroll_depth_75 1.0 · add_to_wishlist 5.0 · cta_click 6.0
conversion 10.0 · rec_click +4.0 · rec_dismiss −3.0     (+2.0 dwell bonus > 30 s)
```

**Two decay constants ≈ two memories:**

- λ_long = ln2/72 h — enduring interests
- λ_short = ln2/6 h — what the user is doing *right now*
- merged = `0.6·long + 0.4·short`

Both vectors reach the generate prompt, which is what lets a card say "you've been circling agentic
architectures all week — and this morning you went deep on LangGraph." That is the persuasion
requirement, served by arithmetic.

**The feedback loop is the same code.** `rec_click` and `rec_dismiss` are events with weights, so
clicking a card strengthens that category and dismissing one weakens it. No second model, no
training. And because negatives ride the same `exp(−λ·age)` decay as positives, a two-month-old
dismissal is already ≈ 0 — the system forgives.

### 5.1 Fingerprint and trigger policy — the planner

The fingerprint is a hash over the merged interest vector. The trigger policy decides whether the
agent runs at all:

**Run iff** cosine distance from the last fingerprint > `0.15` · **or** ≥ 8 significant events since
the last run · **or** the current rec is stale (> 6 h) and the user is active · **or** a high-intent
event fired (`cta_click`, `conversion`, `add_to_wishlist`).

**Suppress** on a 90 s debounce, on an in-flight run for that user (per-user lock, so concurrent
requests coalesce onto one run), and below the cold-start floor of 3 events — under which the user
gets trending-within-observed-signal, never generic popularity.

Unchanged fingerprint → the cached card is served at **zero LLM calls**.

> **On "is this really agentic?"** The planner here is deliberately not an LLM. The trigger policy —
> fingerprint delta, event thresholds, high-intent signals, debounce, in-flight coalescing — *is* a
> planner; it decides whether reasoning is worth paying for. A system that puts an LLM in the
> planning seat spends tokens deciding whether to spend tokens. This one plans in microseconds and
> reasons only when behavior has materially changed. The ablation row measures the difference.

---

## 6. The agent — two LLM calls on the happy path

LangGraph, checkpointed to SQLite, traced to LangSmith when `LANGSMITH_TRACING=true`.

```
load_state
    │
analyze_behavior         ← LLM #1 (MODEL_FAST, json_schema, fallback chain §7)
    │                       input: merged + dual-horizon interest vectors
route_signal ─ insufficient ─► cold_start ─► persist
    │
retrieve                 ← per-interest Chroma query + FTS5 query, fused by RRF
    │
fusion_rank              ← deterministic (§3.2) → top 8
    │
deterministic_grade      ← pure Python: do the top-5 candidates cover the
    │                       inferred categories?
    ├─ ratio == 1.0 ─────────────────► generate
    ├─ ratio == 0.0 ─► refine_query (1 loop max) ─► retrieve
    └─ 0 < ratio < 1 ─► llm_grade (rare) ─► generate | refine
    │
generate                 ← LLM #2 (MODEL_WRITER): narrative referencing both
    │                       horizons, + per-item reason and hook.
    │                       T3 persuasion facts injected here, verbatim.
    │
validate                 ← IDs ⊆ retrieved set · active · ≤ 5 · deduped ·
    │                       reasons ≤ 220 chars.  THE GROUNDING GUARANTEE.
persist → card
```

**Deterministic-first grading** is why the happy path is two calls and not three. A Python coverage
check answers "did retrieval find what the user cares about?" in microseconds; the LLM grader runs
only when that check is genuinely ambiguous. It can therefore never fire on hallucinated
insufficiency, and the refine loop is capped at one iteration.

**Confidence is computed, never asked of the model:**

```
confidence = 0.5·norm(fusion_score) + 0.35·interest_match + 0.15·level_match
```

Weighted-additive rather than multiplicative: multiplying five [0,1] terms collapses everything
toward zero (0.8⁵ ≈ 0.33 reads as "poor" for an excellent match) and one noisy term nukes the score.

**The card:**

```
┌─ Agentic AI Bootcamp ────────────────────────── 92% match ─┐
│ "You've been circling agentic architectures all week —     │
│  and this morning you went deep on LangGraph twice."       │
│  ✓ 3 product views in Agentic AI   ✓ searched 'langgraph'  │
│  ✓ live cohort opens 6 Sep         ✓ intermediate level    │
│  Next step: Advanced LangGraph Patterns →                  │
│                                    [View course]  [✕]      │
└────────────────────────────────────────────────────────────┘
```

Explanations are generated **once**, inside `generate`, and stored on the card. There is no
`/explain` endpoint that re-calls the model — that would contradict the entire efficiency argument
for zero judge-visible gain.

---

## 7. Mesh integration

All LLM and embedding calls go through Mesh (`MESH_BASE_URL`), with retry and exponential backoff.

**The structured-output fallback chain** is the single highest-probability catastrophic failure in
this design, so it has three layers of defense:

1. `MODEL_FAST` with `response_format` — the happy path.
2. On parse failure or missing fields, one retry against `MODEL_FAST_FALLBACK`.
3. A **fence-stripping parser** that trims ```` ```json ```` fences and extracts the outermost `{…}`
   before `json.loads` — the last line of defense for when neither model honors `response_format`.

Which path fired is logged to `agent_runs.fallback_used`. Structured-output support is confirmed
against `GET /v1/models` **at startup** and logged, rather than assumed.

`embed_batch(texts)` is a single POST — seeding the catalog is one or two calls, not one per course.
`embedding_cache` keys on text hash so unchanged content is never re-embedded.

---

## 8. Scheduler

APScheduler, single worker, behind `SCHEDULER_ENABLED`:

| Every | Job | Status |
|---|---|---|
| 30 s | Drain `vector_outbox` in chunks of 20; a transient failure retries per-item, a fatal one halts (§3.4). | **built** |
| 15 min | Refresh stale recommendations for **active users only**. | pending |
| 03:00 | Reconcile SQLite ↔ Chroma · `wal_checkpoint(TRUNCATE)` · delete orphaned anonymous events > 90 days. | partial — checkpoint only |
| 16:00 | Digest email — **bonus tier, first to be cut.** | pending |

The drain job is registered `max_instances=1, coalesce=True`: a drain slower than its 30 s interval
must not overlap itself and embed the same product twice concurrently. It logs only when it did
something, so an idle queue doesn't fill the log every half minute.

`POST /admin/trigger-digest` renders the digest on demand and ships regardless of whether the cron
survives; the manual trigger is all the demo needs.

---

## 9. Evaluation

Seven personas: the original four plus `confused`, `price_sensitive`, and `two_interests` — the last
directly validates the multi-query + RRF design, since a blended-query implementation fails it.

Metrics: relevance, grounding rate (must be 100% — the validate node makes it a guarantee, so any
other number is a bug), **Coverage** (% of catalog ever recommended across personas), **Diversity@5**
(distinct categories per set), LLM calls per recommendation, and p95 tracking latency.

Ablations, one run each: trigger-policy off/on · fusion vs raw RRF · grade-path off/on.

**Every number in the README is measured or labeled.** Single runs are reported as single runs and
called directional. Fabricated rigor is worse than honest imprecision, and a five-seed mean±std that
nobody actually ran is fabricated rigor.

---

## 10. Build order

| When | Work | Green means |
|---|---|---|
| **Aug 6** | **Foundation (§1): auth + roles + admin CRUD.** Wire routers into `main.py`. Browse/search/course pages. | Register → login → admin adds a course → it is findable by search and by similarity. |
| **Aug 6 PM** | `tracker.js` + `/api/events` queue/writer + stats + identity stitching. Then scorer + fingerprint + trigger policy + rec cache. | Browse → events land, p95 measured. Fingerprint moves with behavior. |
| **Aug 7** | LangGraph: analyze → retrieve → fusion_rank → grade → generate → validate. Mesh fallback chain. Cards on the site. **Go/no-go at EOD.** | Behavior visibly drives grounded cards. Traces in LangSmith. |
| **Aug 8 AM** | Outbox drain upgrade (or document synchronous dual-write and move on). `agent_runs` admin page. | Outbox drains, or the fallback is documented. |
| **Aug 8 PM** | Eval harness + ablations + README + demo video. Digest manual trigger. | README numbers are real. Video recorded. |
| **Aug 9 by 10:00** | Buffer, final push. | Submitted two hours early. |

**Cut order under pressure:** digest cron → outbox upgrade → cross-encoder ablation → `agent_runs`
page → deploy.

**Never cut:** the auth/role foundation · dual-write in either form · non-blocking tracking · the
validate node · the trigger policy · measured efficiency numbers.

---

## 11. Deliberately not built

Each of these is a real idea rejected for a stated reason, not an oversight. They belong in the
README as trade-offs, because naming what you did not build and why is more credible than a feature
list.

| Not built | Why |
|---|---|
| **LLM planner agent** | The deterministic trigger policy *is* the planner, and that is the thesis (§5.1). An LLM planner pays tokens to decide whether to pay tokens. |
| **Full knowledge graph** | Two slug arrays capture the demo value at 5% of the cost. Neo4j in four days is how submissions die. |
| **Learned ranker (LightGBM)** | Needs interaction data that does not exist. A ranker trained on synthetic personas would be theater; documented fusion weights are honest and explainable. |
| **Kafka / Redis Streams / feature store** | The asyncio queue *is* the stream at this scale. One README sentence names the swap. |
| **Collaborative filtering** | Cold-start platform, a handful of seeded personas — there are no similar users yet. |
| **Conversation memory** | There is no conversation in this product. Users browse; they never chat with the agent. |
| **A "tool layer" over price/inventory/reviews** | Those are columns, not tools. Wrapping `products.price` in a tool call adds latency and failure modes to fetch data retrieval already returned. |
| **Separate `/explain` endpoint** | An extra LLM call per view for an explanation `generate` already produced and stored. |
| **Docker, Alembic, pre-commit, mypy** | `init_db.py` is the migration story; the judge runs `setup.sh`. `ruff` alone carries 80% of the lint value. |
| **Rate limiting, PII masking, secrets manager** | Admin routes are role-gated; the rest is a security paragraph at this scale, not code. |

---

## 12. Configuration

`.env` (gitignored; `.env.example` is the tracked template). `SUBMISSION_TOKEN` also lives here for
local reference, but **CI reads it from the GitHub repository secret** — `.env` is never pushed.

```
MESH_API_KEY · MESH_BASE_URL
MODEL_FAST=google/gemini-2.5-flash · MODEL_FAST_FALLBACK=openai/gpt-4o-mini
MODEL_WRITER=openai/gpt-4o · EMBED_MODEL=openai/text-embedding-3-small

SECRET_KEY · DATABASE_URL · CHROMA_DIR · ENV

RERANK_MODE=fusion · FINGERPRINT_COS_THRESHOLD=0.15 · TRIGGER_MIN_EVENTS=8
TRIGGER_DEBOUNCE_S=90 · REC_STALE_HOURS=6 · COLD_START_MIN_EVENTS=3

SCHEDULER_ENABLED · DIGEST_HOUR · SMTP_*
LANGSMITH_TRACING · LANGSMITH_API_KEY · LANGSMITH_PROJECT
```

`settings.use_mesh` is false when `ENV=test` or no key is set, so the test suite runs offline.

**CI:** `.github/workflows/smartreco-build-challenge-2026-checks.yml` is platform-supplied and
validated server-side. Do not edit it. It executes remote code with repository secrets in the
environment, so the Mesh key registered there should be a throwaway with a spend cap.

```bash
make init       # schema + FTS5 + triggers + indexes
make validate   # course JSON structure + curation rules
make catalogue  # regenerate the curation tracker
make ingest     # chunk → embed → Chroma + catalog.index.json
make dev        # uvicorn --reload
make test       # pytest
make lint       # ruff

python -m app.catalog.sync_sql             # data/data_1 → products table (no Mesh key needed)
python -m app.catalog.outbox               # drain vector_outbox → Chroma on demand
python -m app.auth.cli create-admin EMAIL  # mint the first admin
python -m app.auth.cli make-admin EMAIL    # promote an existing user

node tests/tracker/test_tracker.js         # tracker.js behavior (no browser needed)
```

`make` is not installed on a default Windows box; every target has a direct equivalent, tabulated in
the README. Test dependencies are in the `dev` extra and are not installed by default.

**Operational note — embeddings are a paid call.** Listing models is free, so the startup check can
report "all 4 configured models available" while every embedding attempt returns
`402 spend_limit_exceeded`. When that happens the outbox drain halts by design and holds its rows;
`/admin/sync` shows the queue depth and the reason. Nothing is lost and no attempts are burned.
