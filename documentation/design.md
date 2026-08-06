# SmartReco — Foundation Design

**Scope of this document.** The platform layer: the web application, authentication, roles, the
database schema, user profiles, the product dual-write, and behavioral event tracking. It describes
what is *built and verified* as of 6 Aug 2026. The retrieval, ranking and agent layers are specified
in [`SmartReco_Architecture_v2_FINAL.md`](../SmartReco_Architecture_v2_FINAL.md) §2–§9; where this
document touches them it says so and links there rather than repeating.

Status of everything below: **implemented and exercised end to end** — 126 assertions passing across
four layers (§10).

---

## 1. What the foundation had to satisfy

> A working web application with email/password login (keep auth simple) and two roles: a regular
> user who browses and gets recommendations, and an admin who manages the product catalog.
> A clean database schema with the tables your system needs — users, products, activity/events,
> and stored recommendations — properly related.

Then, building on it:

> **Product Management with Dual-Write.** An admin can add, edit, and delete products/courses.
> Critically: when a product is added, it must be written to both your main database and a vector
> database. The two stores must stay in sync as products change.

> **Behavioral Event Tracking.** Track meaningful user activity on the frontend: page/product views,
> searches, clicks, time spent. Tracking must be efficient and non-blocking. Think about batching,
> throttling high-frequency events, and sending data without freezing the user experience.

Plus user-supplied profile information (resume, bio) — §10.

Each clause is traced to code in §6.

---

## 2. Why server-rendered

FastAPI + Jinja2, server-rendered, no SPA and no Node build step. This is a deliberate choice with
two reasons, and both are load-bearing:

1. **The behavioral tracker needs real page loads to observe.** The entire recommendation thesis is
   that behavior drives recommendations — page views, dwell time, scroll depth, search, clicks. A
   client-side-routed SPA replaces navigations with state transitions, so every one of those signals
   would need re-deriving from router events. The tracking design (arch §4) assumes documents.
2. **A four-day window has no room for a second toolchain.** The deadline is 9 Aug 2026. A Node
   build pipeline is another runtime to install, build, and deploy, for a UI whose job is to render
   twelve course cards and six forms.

The cost is honest: no client-side interactivity beyond what vanilla JS in `tracker.js` provides.
For this application that cost is close to zero.

---

## 3. Authentication

Email + password. No OAuth, no magic links, no email verification, no password reset. "Keep auth
simple" was the requirement and simple is what serves a four-day demo.

### 3.1 Password hashing — bcrypt directly, not passlib

The original plan said `passlib[bcrypt]`. That does not work against the installed bcrypt:

- passlib 1.7.4 (last release 2020, unmaintained) reads `bcrypt.__about__.__version__` at import to
  detect its backend. `bcrypt` ≥ 4.1 removed `__about__`, so this raises `AttributeError`.
- It then hands `hashpw` a 72-byte-padded config string that bcrypt 5 rejects outright with
  `ValueError: password cannot be longer than 72 bytes`.

The net effect was that **every registration returned HTTP 200 with no user created** — a failure
that only surfaced when the flow was actually exercised. We call `bcrypt.hashpw` / `bcrypt.checkpw`
directly; the API surface we need is two functions.

**Over-length passwords are rejected, not truncated.** bcrypt silently ignores bytes past 72, which
means a user with a 100-byte passphrase would have the last 28 bytes do nothing, invisibly. We raise
instead. `verify_password` returns `False` rather than raising on a malformed stored hash, so a
corrupt row is a failed login, not a 500.

### 3.2 The session cookie

A signed cookie via `itsdangerous` (`sr_session`), carrying `{uid, role}`, 14-day max age. There is
no server-side session table because there is nothing in a session worth a table.

Flags: `HttpOnly`, `SameSite=lax`, and `Secure` **derived from the request scheme, not from `ENV`**.

This last point is a bug we hit and fixed. The original spec said `Secure` when `ENV != development`.
Under that rule, any non-development deployment served over plain HTTP — a staging box, a demo
container, anything behind a proxy that hasn't got TLS yet — sets a `Secure` cookie that the browser
then refuses to send back. Login appears to succeed (303 + `Set-Cookie`), and every subsequent
request is silently anonymous. It is a nasty failure because nothing errors.

`request.url.scheme` already reflects `X-Forwarded-Proto` when uvicorn runs with `--proxy-headers`,
which is the standard TLS-terminating-proxy setup, so this is correct in production too. When there
is no request to inspect the fallback is `ENV not in (development, test)`.

### 3.3 Role is read from the database, not the cookie

The cookie carries `role`, but `identity_middleware` re-reads it from `users` on every authenticated
request. A cookie signed before a promotion or demotion otherwise carries the stale role for up to
14 days — someone demoted from admin would keep seeing admin UI until their cookie expired.

One indexed primary-key lookup per authenticated request is the right price for not serving a stale
role. If the user row is gone (deleted since the cookie was signed), the request is treated as
anonymous rather than 500-ing.

### 3.4 Not leaking which half was wrong

Failed login returns one message — "Incorrect email or password" — for both an unknown email and a
wrong password. Distinguishing them turns the login form into an account-enumeration oracle.

### 3.5 Anonymous browsing and identity stitching

Visitors browse without an account and are tracked by a `sid` cookie (not `HttpOnly` — `tracker.js`
reads it). On register **or** login, `_stitch_session_events` backfills `user_id` onto that
browser's events:

```sql
UPDATE events SET user_id = :uid
WHERE session_id = :sid AND user_id IS NULL
```

The `user_id IS NULL` clause matters: without it, a shared browser would re-attribute a previous
user's events to whoever logs in next. Logout clears the `sid` cookie for the same reason.

This is what lets a brand-new account get a first recommendation grounded in what the person did
*before* they signed up.

---

## 4. Roles and authorization

Two roles, one column: `users.role ∈ {user, admin}`.

| Role | Can do |
|---|---|
| `user` | Browse and search the catalog, view course pages, receive recommendations, click/dismiss recommendation cards |
| `admin` | Everything a user can, plus product CRUD, the ingest trigger, and the `agent_runs` observability page |

**Enforcement is one router-level dependency, not per-handler checks:**

```python
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
```

Per-handler decorators are how one handler eventually gets missed. With the dependency on the
router, a new admin route is protected by existing, not by remembering. `require_admin` returns 401
when logged out and 403 when logged in without the role — the distinction matters for the UI.

Registration always produces `role='user'`. The first admin is minted from the CLI:

```bash
python -m app.auth.cli create-admin you@example.com   # new admin
python -m app.auth.cli make-admin you@example.com     # promote existing user
```

Without this there is no path to a first admin short of hand-editing SQLite.

---

## 5. Database schema

SQLite in WAL mode. `app/db/session.py` sets the pragmas on every connect: `journal_mode=WAL`,
`synchronous=NORMAL`, 64 MB cache, `temp_store=MEMORY`, `foreign_keys=ON`. One writer, many readers
is exactly this workload.

### 5.1 Relationships

```
users ──1:1──► user_profiles      derived interest state, one row per user
  │
  ├──1:N──► events                the raw behavioral log (user_id NULLABLE)
  │
  └──1:N──► recommendations       stored rec sets, one current per user

products ──1:N──► vector_outbox   pending Chroma sync work
   │
   └── products_fts               FTS5 virtual table, trigger-maintained

agent_runs        one row per agent invocation (observability)
embedding_cache   text_hash → vector
digest_log        (user_id, sent_date) unique — digest idempotency
```

Verified against the live database:

| Table | Foreign key | Indexes |
|---|---|---|
| `users` | — | `email` unique |
| `products` | — | `slug` unique, `category` |
| `events` | `user_id → users.id` | `event_uuid` unique, `(user_id, ts)`, `(session_id, ts)` |
| `user_profiles` | `user_id → users.id` | `user_id` unique (enforces 1:1) |
| `recommendations` | `user_id → users.id` | `(user_id, is_current)` |
| `vector_outbox` | — | `(status, created_at)`, partial on `attempts` |

### 5.2 The four required tables

**`users`** — `id`, `email` (unique), `password_hash`, `role`, `digest_opt_in`, `created_at`.
Email is stored lowercased and compared lowercased, so `U@Ex.com` and `u@ex.com` are one account.

**`products`** — the course catalog. `slug` is the stable identity and what ladder edges point at;
`id` is an implementation detail. `prereq_ids` / `related_ids` are JSON slug arrays forming the
learning ladder (arch §3.4). `content_hash` is what makes re-ingest cheap: unchanged courses are
skipped rather than re-embedded. Soft delete via `is_active` — a hard delete would orphan the events
and recommendations that reference the row.

**`events`** — the behavioral log, and the source of truth for everything the recommender knows.

- `event_uuid` is unique and client-generated, so retries are idempotent. This matters because the
  tracker uses `sendBeacon`, which can duplicate on page unload. The writer uses
  `INSERT ... ON CONFLICT (event_uuid) DO NOTHING`.
- `user_id` is **nullable** — that is what makes anonymous tracking possible, and it is the column
  stitching fills in.
- `session_id` is always present.
- Indexed `(user_id, ts)` and `(session_id, ts)` because every read is "recent events for this
  identity."

Event types: `page_view`, `product_view`, `product_dwell`, `search`, `search_result_click`,
`category_filter`, `add_to_wishlist`, `cta_click`, `scroll_depth`, `conversion`, `rec_click`,
`rec_dismiss`.

**`recommendations`** — `narrative` plus an `items` JSON array of
`{product_id, reason, hook, confidence, next_step_id, rank}`, with the `fingerprint` it was
generated for, `trigger_reason`, `model_used`, `token_cost`, and `is_current`. Superseded sets are
kept with `is_current=false`: they are the recency-penalty input (arch §5.3) and the audit trail.

`user_profiles` holds two kinds of data with different durability requirements (§9.2). The
**derived** half — `interests`, `fingerprint`, `events_seen` — is never authoritative and is fully
rebuildable from `events`; the behavioral log is the thing that must not be lost. The **declared**
half — `bio`, `goals`, `skills`, `resume_text` — is the opposite: the user typed it, nothing else
can regenerate it, and losing it means asking them to type it again. Any future "recompute
profiles" job must therefore rewrite only the derived columns.

### 5.3 Schema creation

`python -m app.db.init_db` creates tables, the FTS5 virtual table, its sync triggers, and the outbox
indexes. There is no migration tool; `init_db` is the migration story for a greenfield app with a
four-day life.

The FTS5 table is `content=products, content_rowid=id` (external-content), so `products_fts.rowid`
*is* `products.id` and search joins straight back. Three triggers (`_ai`, `_ad`, `_au`) keep it in
sync on insert/delete/update — verified: editing a product's title through the admin UI makes it
findable by the new title immediately.

---

## 6. Requirement → code

| Requirement | Where |
|---|---|
| Working web application | `app/main.py`, `app/web/`, Jinja2 templates |
| Email/password login | `app/auth/routes.py`, `app/auth/security.py` |
| Two roles | `users.role`; `require_admin` in `app/auth/deps.py` |
| Regular user browses | `app/catalog/routes.py` — `/`, `/search`, `/course/{slug}` |
| Regular user gets recommendations | `app/web/routes.py` — `/recommendations` |
| Admin manages catalog | `app/admin/routes.py` — full CRUD, ingest, agent-runs |
| users, products, events, recommendations | `app/db/models.py`, related as §5.1 |
| Dual-write to a vector DB | `app/admin/routes.py` (enqueue) + `app/catalog/outbox.py` (drain) + `app/catalog/vectors.py` (Chroma) — §7 |
| Stores stay in sync as products change | `content_hash` skip, last-op collapse, 30 s drain in `app/scheduler/jobs.py`, `/admin/sync` — §7.3–7.5 |
| Frontend event tracking | `app/web/static/tracker.js` — §8 |
| Non-blocking / batched / throttled | 202 + `asyncio.Queue`; 10-event or 5 s batches; 150 ms scroll sampling — §8.1–8.2 |
| Event schema (who/what/when) | `Event` in `app/db/models.py` — §8.5 |
| User information (resume, bio) | `app/profiles/routes.py`, `app/profiles/resume.py` — §9 |

### Route map

```
GET  /                     landing / browse (+ category, level filters)
GET  /search?q=            FTS5 catalog search
GET  /course/{slug}        course detail — the main tracked surface
GET  /recommendations      stored rec cards (login required)

GET  POST /auth/register
GET  POST /auth/login
POST /auth/logout

GET  POST /profile         declared profile (bio, goals, skills, resume)
GET  /profile/resume       download your own resume
POST /profile/resume/delete

POST /api/events           tracker ingest → 202
GET  /api/events/stats     queue depth, processed today, dropped

GET  /admin                          dashboard (counts)
GET  /admin/products                 list (+ ?include_inactive)
GET  POST /admin/products/new        create
GET  POST /admin/products/{id}/edit  update
POST /admin/products/{id}/delete     soft delete
POST /admin/ingest                   re-run catalog ingest
GET  /admin/sync                     SQL vs Chroma, queue depth
POST /admin/sync                     drain the outbox now
GET  /admin/agent-runs               observability

GET  /healthz  /health     liveness
```

Router order in `app/main.py` is significant: the catalog router is included **last** because it
owns `/` and `/course/{slug}`, the broadest patterns.

`/recommendations` deliberately **does not invoke the agent**. Generation is trigger-driven
(arch §5.1); putting an LLM call on the critical path of a page load is precisely what the planner
exists to avoid. The page reads the stored current set, and drops any card whose product has since
been deactivated rather than rendering a broken link.

---

## 7. Product management and the dual-write

### 7.1 Why not write to both stores directly

The obvious implementation — insert the product, then call Chroma — is wrong, and the reason is
that **two stores cannot be committed atomically**. A crash between the two writes leaves SQLite
with a product the vector store has never heard of, and *no record that the write was owed*. The
divergence is permanent and silent: nothing later can tell that the product is missing from Chroma,
because nothing recorded that it should be there.

The outbox pattern converts "write to two stores" into "write to one store, then replay":

```
admin form ──► BEGIN
                 INSERT products …
                 INSERT vector_outbox (product_id, op='upsert', status='pending')
               COMMIT                          ◄── one transaction, one durability boundary
                     │
   scheduler (30s) ──┤
                     ▼
               drain_once() ──► embed (batched) ──► Chroma upsert ──► status='done'
```

The intent to sync is now as durable as the product itself. Delivery is at-least-once, which is
safe because Chroma upserts are idempotent — a replayed row overwrites itself.

### 7.2 What enqueues what

Every mutating admin operation enqueues a `vector_outbox` row **in the same transaction** as the
product write (`app/admin/routes.py`):

| Action | Product | Outbox |
|---|---|---|
| Create | insert | `upsert` |
| Edit (active) | update, `content_hash=""` | `upsert` |
| Edit (deactivate) | `is_active=false` | `delete` |
| Soft delete | `is_active=false` | `delete` |

### 7.3 Draining — `app/catalog/outbox.py`

Four decisions worth stating:

**Collapse to the last op per product.** Five edits between drains are one upsert of the current
state, not five embeds of superseded versions.

**Skip unchanged content.** `content_hash` is the SHA-256 of the embedded text. An admin editing
`price` — which is deliberately *not* embedded, per arch §3 — costs zero API calls. Verified by
test: a second drain of an untouched product performs no upsert.

**Fatal vs transient failures.** A `402` (no balance), `401`/`403` (bad key) or `404` (no such
model) means every item in the batch will fail identically. The first version retried each item
individually on any failure, which turned one refusal into *N+1 charged API calls per drain* — this
was found by running against the real account and watching 13 products generate 13 identical 402s.
Fatal statuses now halt the drain, leave rows `pending` **without charging an attempt** (the work is
still owed and will succeed unchanged once the account is topped up), and surface an actionable
message. Transient failures (429, 5xx) still fall back to per-item retry so one poison row cannot
block the queue behind it.

**Bounded retries.** Past `MAX_ATTEMPTS = 5` a row is parked as `failed` with its error, visible at
`/admin/sync` rather than retried forever.

### 7.4 Two write paths, one collection

| Path | Source | Chunks | Id |
|---|---|---|---|
| `app/catalog/ingest.py` | curated `data/data_1/*.json` | many per course (objectives, module groups) | `{slug}::{type}::{n}` |
| `app/catalog/vectors.py` | admin form → SQL row | one per product | `product::{id}::0` |

Admin-created products have no JSON file behind them, so they cannot be chunked the same way. Both
paths write the same metadata keys into the same collection, so retrieval never needs to know which
produced a row. Deletes are issued `where={"parent_id": id}` rather than by id, so deactivating a
curated course removes *all* its chunks, not just the one an admin-form delete would know about.

This is why the chunk count at `/admin/sync` is normally higher than the product count — stated on
that page, so the difference doesn't read as drift.

### 7.5 Visibility

`GET /admin/sync` shows active products, chunks in Chroma, and queue depth by status side by side.
Divergence should be observable, not inferred. `POST /admin/sync` drains on demand, so an admin who
just edited a product can confirm it landed instead of waiting 30 seconds and hoping.

---

## 8. Behavioral event tracking

### 8.1 The constraint that shapes everything

> Tracking must be efficient and non-blocking — it must not slow down or break the frontend.

Three rules follow, and every choice in `app/web/static/tracker.js` is one of them:

**Never block.** Events go into an in-memory array; the network happens on a timer, never on the
interaction itself. No handler awaits a POST — a click that waits for the network is a click that
feels slow. Verified: a page load performs **zero** network calls.

**Never lose the last batch.** A user who reads a page and closes the tab produces the most
interesting event — a long dwell — at the exact moment a normal `fetch` is killed. `sendBeacon`
survives unload; `fetch` does not. It also cannot set headers, which is why the session rides the
`sid` **cookie** (arch trap #5) — anything header-based would silently lose precisely the unload
events we most want.

**Never break the page.** The file is one try/catch-wrapped IIFE and every send is fire-and-forget
with a swallowed rejection. An analytics bug must not take the product down with it.

### 8.2 Batching and throttling

| Knob | Value | Why |
|---|---|---|
| `MAX_BATCH` | 10 events | flush early when a user is active |
| `FLUSH_MS` | 5 s | bound the loss window when they are not |
| `QUEUE_CAP` | 200 | a runaway page cannot exhaust memory |
| `SCROLL_SAMPLE_MS` | 150 ms | scroll fires at refresh rate; sampling is what keeps it off the main thread |

Scroll is the high-frequency case the requirement calls out. A raw `scroll` handler is the classic
jank source, so the listener is `{passive: true}` (it can never block scrolling), sets a flag, and
samples on a timer. It then emits **only the highest new milestone** of 25/50/75/100 — flicking from
top to bottom produces *one* event, not four, and scrolling back up produces none. Verified: 50 raw
scroll events yield 0 tracked events; a 0→100% jump yields exactly 1, with `depth: 100`.

### 8.3 Dwell

Measured as time the page is **visible**, not wall-clock since load — a tab left open overnight is
not thirty thousand seconds of interest. The timer pauses on `visibilitychange → hidden` and resumes
on show. Anything under 1 s is a bounce and is not recorded; anything over 30 min is discarded as a
stuck timer.

Both `visibilitychange` and `pagehide` flush, because neither alone is sufficient: `unload` does not
fire when a mobile browser backgrounds an app, and `visibilitychange` can be skipped on bfcache
navigation.

### 8.4 Delivery guarantees

Failure is handled rather than assumed away. A rejected `fetch` or a refused `sendBeacon` puts the
batch **back** on the queue for the next flush, bounded by `QUEUE_CAP` — so an offline stretch
degrades to dropping the oldest events instead of growing without limit. Every event carries a
`crypto.randomUUID()`, and the server inserts with `ON CONFLICT (event_uuid) DO NOTHING`, so the
retries (and `sendBeacon`'s own duplicate-on-unload behavior) cannot double-count.

### 8.5 Event types and schema

`page_view`, `product_view`, `product_dwell`, `page_dwell`, `scroll_depth`, `search`,
`search_result_click`, `product_click`, `rec_click`, `rec_dismiss`, `cta_click`, `category_filter`.

Who / what / when, in `events`: `user_id` (null when anonymous), `session_id` (the `sid` cookie),
`event_type`, `product_id`, `query`, `dwell_ms`, `meta` (JSON), `ts`. Indexed on `(user_id, ts)` and
`(session_id, ts)` — both read patterns of the interest model.

Server side: `POST /api/events` validates and enqueues, returning **202 immediately**; a background
writer batches to SQLite at 200 rows or 1 s. The handler never touches the database.

### 8.6 How it is verified without a browser

No Playwright or Selenium is installed, and adding a browser stack three days before the deadline
was not worth it. Instead the tracker is executed in Node under `vm` against a minimal DOM stub, and
its *behavior* is asserted: that page load makes no network call, that 10 events trigger a flush,
that 50 scroll events produce none, that hiding the tab uses `sendBeacon`, that a 300 ms visit emits
no dwell, that a failed send is re-queued, that the queue is capped. 24 assertions.

The contract between the two halves is then checked for real: the exact JSON `tracker.js` emits is
POSTed to a running server, and the rows are read back out of SQLite.

---

## 9. User profile — declared information

### 9.1 Why collect it at all

On a brand-new account there is no behavior. The interest model needs events that do not exist yet,
so the recommendation for that person is either nothing or a popularity list — the exact failure the
project exists to avoid. A stated background is the only signal available at t=0.

### 9.2 Declared and derived are kept apart

`user_profiles` holds both, in clearly separated blocks:

| Kind | Columns | Written by |
|---|---|---|
| Derived | `interests`, `interests_short`, `fingerprint`, `events_seen`, `llm_summary` | the interest model, from events |
| Declared | `full_name`, `headline`, `bio`, `goals`, `skills`, `experience_years`, `resume_*` | the user, via `/profile` |

They age and are trusted differently: behavior is current but narrow, a resume is broad but stale
the day after it is written. The profile router touches **only** the declared columns; it can never
corrupt the derived model. The profile page renders the derived side read-only, so a user can see
what their behavior says without being able to edit it.

### 9.3 Resume handling

The extracted **text** is stored alongside the file, because the text is what an LLM can use — and
re-parsing a PDF on every read to recover it would be absurd. `.txt`/`.md` are read natively.

No PDF library is a dependency. That is a deliberate scope decision (four days, and a resume's value
here is its text), and it makes the failure mode the important part: **an extractor that returned
`""` on an unreadable PDF would leave the user with a green checkmark, a stored file, and an empty
profile the agent can never use.** So extraction failure is loud — the upload still succeeds and the
file is kept, but the page says text could not be extracted and points at the paste box. If `pypdf`
happens to be installed it is used; a scanned PDF with no text layer says so specifically.

Uploads are capped at 2 MB and 20,000 characters. Filenames are attacker-controlled, so only the
basename is taken and non-alphanumerics are replaced — `../../../../etc/passwd` becomes `passwd` —
and files are stored as `user_{id}{ext}` regardless, so a crafted name cannot choose its own path.
The download route reads the path from the user's own row, never from the request.

### 9.4 Partial vs full submit

The full form posts a hidden `form=full`, meaning "these values are the complete truth" — so
clearing a box really does erase the stored value. Any other POST is treated as partial and leaves
unsent fields alone.

Without this distinction the form is all-or-nothing, and uploading a resume from a page whose other
inputs happen to be empty silently wipes the user's bio. That is a data-loss bug, and it is exactly
what the first version did; it was caught by the end-to-end test and is now covered from both sides
(a partial submit preserves; a full submit clears).

---

## 10. Verification

Layered, because different things need different proof:

| Layer | Command | What it covers | Count |
|---|---|---|---|
| pytest | `python -m pytest tests/ -q` | profile logic, resume extraction, traversal, outbox mechanics, freshness, smoke | **60** |
| Node + DOM stub | `node tests/tracker/test_tracker.js` | tracker.js batching, throttling, beacon, dwell, retry, cap | **24** |
| Dual-write probe | scratch | create → outbox → Chroma → semantic query → edit → delete, against real Chroma | **11** |
| Live uvicorn | scratch | full journey against a real server and real SQLite | **16** |

The pytest suite cleans up after itself — every account, profile and uploaded file it creates is
removed in a fixture teardown. A test suite that slowly fills the app it tests with its own debris
is a test suite people stop running.

The outbox tests fake the embedding call deliberately. What needs proving is the *sync mechanism* —
queued, drained once, skipped when unchanged, retried sanely — and a test that depends on a paid
external API is a test that fails for reasons unrelated to the code it covers. Whether the vector is
a *good* embedding is Mesh's job.

Earlier foundation verification (45 assertions, run as a scratch suite) covered:

- **Anonymous** — landing renders, `sid` cookie issued, search works, a malformed FTS5 query
  (`NEAR("`) returns 200 with a friendly message rather than a 500, unknown slug 404s.
- **Tracking** — event accepted (202), *actually persisted*, `dropped == 0`.
- **Authorization** — `/admin*` 401 logged out, 403 as a regular user, 200 as admin, 401 again
  after logout.
- **Registration** — short password rejected, 303 + cookie on success, duplicate email rejected
  case-insensitively.
- **Stitching** — an event logged while anonymous carries the new `user_id` after registering.
- **Login** — wrong password and unknown email produce the identical message.
- **Roles** — promotion in the DB is reflected immediately, without re-login.
- **CRUD** — create/edit/soft-delete round-trip, duplicate slug rejected, FTS5 reindexed by trigger
  on edit, deactivated course vanishes from the landing page and 404s on detail while remaining
  visible under `?include_inactive=true`, outbox rows queued for each.

Against the **real 12-course catalog**: all 12 render, search returns sensible hits
(`rag` → 10, `agentic` → 9, `langgraph` → 8, `mlops` → 2), ladder links resolve, admin list shows
12 rows.

### Bugs this found

All silent — each returned success while doing nothing, which is why none was visible to any check
short of exercising the real path.

**Found while building the dual-write, profile and tracker:**

1. **`init_db` reported success while changing nothing.** `create_all()` creates missing *tables*;
   it never alters one that already exists. Adding the ten profile columns and re-running printed
   `✓ schema created` and applied none of them — the first query would have failed at runtime with
   `no such column`, long after the command that should have caught it said it was fine. `init_db`
   now reconciles additively and *reports each column it adds*. It is still not a migration tool
   (no renames, drops or type changes) and says so.
2. **Retrying an unretryable error N+1 times.** Found against the real Mesh account: a `402` caused
   the batch to fail, then each of 13 products to be retried individually, five times over. 66
   charged calls to receive the same refusal. Fixed in §7.3.
3. **A partial profile submit wiped fields the user never touched** — §9.4.
4. **The Mesh startup check verified nothing.** Mesh returns a bare JSON array from `/v1/models`,
   not OpenAI's `{"object": "list", "data": [...]}` envelope, so the SDK's pagination wrapper raised
   on `.data`. The exception was caught and logged as a warning, which made it look cosmetic — while
   the check's entire purpose, confirming the configured models exist, had never once run. Now reads
   the endpoint directly, accepts both shapes, and covers `EMBED_MODEL` too.

**Found earlier, in the foundation** — three, two pre-existing in the tracking layer:

1. **No event ever persisted.** `/api/events` declared `ts: str` while `Event.ts` is a `DateTime`
   column; SQLite's driver rejects a string, so every batch insert raised `TypeError` in the
   background writer. The handler had already returned 202, so nothing surfaced to the client. The
   behavioral tracking layer — the foundation of the entire product — was recording zero rows.
   Fixed by parsing to `datetime` in the Pydantic model and normalizing tz-aware client timestamps
   to naive UTC to match the rest of the schema.
2. **Failed batches were uncounted.** The writer logged the exception but did not increment
   `DROPPED`, so `/api/events/stats` reported a healthy queue while data vanished.
3. **`Secure` cookie keyed off `ENV`** — §3.2.

Plus the passlib/bcrypt incompatibility in §3.1.

**The pattern, across all eight:** every one reported success while doing nothing. Four returned
HTTP 2xx, two printed a `✓`, one logged a warning that looked cosmetic, and one silently discarded
every row it was given. None would have been caught by reading the code — only by running the real
path and then *checking the result rather than the return value*. That is why the verification in
this section asserts on persisted state (rows in SQLite, ids in Chroma, columns in `PRAGMA
table_info`) rather than on status codes.

---

## 11. Deliberately not built

- **No React / SPA / Node build** — §2.
- **No PDF/Word text extraction** — §9.3. Files are stored; text is pasted.
- **No embedding of profile text into the vector store.** The resume is stored and readable by the
  agent at generate time; making users semantically searchable is a different feature with different
  privacy implications.
- **No password reset or email verification** — no mail infrastructure in scope for the auth layer,
  and "keep auth simple" was explicit.
- **No CSRF tokens.** `SameSite=lax` blocks cross-site form POSTs, which is the realistic threat
  for a demo with no third-party embedding. A production build would add them.
- **No rate limiting on login.** Worth adding before real users; not before Aug 9.
- **No server-side sessions** — §3.2.
- **No pagination** — 12 courses, capped at 24 per page. Revisit past ~100.
- **No migrations** — §5.3.
