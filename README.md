 

# SmartReco — Behavioral AI Recommendation Agent

A course-recommendation platform that watches what a user actually does — pages browsed, searches
run, time spent, cards clicked and dismissed — and turns that behavior into a small set of grounded,
persuasive recommendations.

The thesis in one sentence: **the expensive part (an LLM) runs only when cheap deterministic code
has established that behavior materially changed.**

> **Build status — 6 Aug 2026.** Built and verified: the platform foundation (web app, auth, roles,
> schema, catalog browsing, admin CRUD), **user profiles** (bio, goals, skills, resume),
> **product management with dual-write** to Chroma, and **behavioral event tracking** end to end
> (browser → queue → SQLite). The retrieval, ranking and agent layers are designed and partially
> implemented; sections marked _pending_ below will carry measured numbers once those land. No
> number in this README is estimated — anything not yet measured says so.

- **Design of the foundation:** [`documentation/design.md`](documentation/design.md)
- **Full system architecture:** [`SmartReco_Architecture_v2_FINAL.md`](SmartReco_Architecture_v2_FINAL.md)

---

## 1. What it is

Three claims the finished build must demonstrate:

1. **Behavior drives recommendations** — not a static popularity list with an LLM writing captions.
2. **Recommendations are grounded** — every recommended course ID came from retrieval over the real
   catalog, and a validate node enforces it. The model cannot invent a course.
3. **The system is efficient by design** — a deterministic planner gates LLM calls, and the numbers
   below are measured from the `agent_runs` table rather than asserted.

_Demo GIF: pending._

---

## 2. Architecture

```
                    ┌─────────────────────────────────────────┐
   browser ────────►│  FastAPI + Jinja2  (server-rendered)     │
   tracker.js       │  auth · catalog · admin · recommendations│
        │           └───────────────┬─────────────────────────┘
        │ POST /api/events (202)    │
        ▼                           ▼
   asyncio.Queue ──► batch writer ──► SQLite (WAL + FTS5) ──► vector_outbox ──► Chroma
                                          │
                                          ├─ interest model (dual-horizon, deterministic)
                                          ├─ fingerprint + trigger policy  ◄── the planner
                                          └─ LangGraph agent (2 LLM calls, happy path)
```

**Stack:** FastAPI · SQLite (WAL + FTS5) · Chroma · LangGraph · APScheduler · Jinja2 · Mesh API ·
LangSmith (optional).

Server-rendered, no SPA, no Node build step — a deliberate choice, reasoned through in
[design.md §2](documentation/design.md).

---

## 3. How we avoid wasteful LLM calls

The design: a deterministic planner decides whether an LLM runs at all. Interest scoring is closed
-form (dual-horizon exponential decay, λ_long = ln2/72h, λ_short = ln2/6h), fusion ranking is a
weighted sum, and confidence is computed rather than asked for. An LLM is invoked only when the
interest fingerprint has moved past a cosine threshold, enough events have accumulated, and a
debounce window has passed.

> A system that puts an LLM in the planning seat spends tokens deciding whether to spend tokens.

Full policy in [arch §5.1](SmartReco_Architecture_v2_FINAL.md).

**Measured numbers: pending.** The `agent_runs` table (`node_path`, `llm_calls`, `retrieval_rounds`,
`cache_hit`, `fallback_used`, `cos_dist`, `latency_ms`) exists and is browsable at
`/admin/agent-runs`; it is empty until the agent runs. Numbers land here, from that table, not from
estimation.

---

## 4. Product management and the dual-write

**Built and verified.** An admin can create, edit and soft-delete courses at `/admin/products`, and
every write lands in **both** SQLite and Chroma.

The two stores can't be committed atomically, so writing to Chroma inline would leave them
permanently diverged after a crash between the two — with no record that the write was owed. Instead
the product row and a `vector_outbox` row are written in **one transaction**, and a drainer replays
the queue into Chroma:

```
admin form ──► BEGIN  INSERT products …
                      INSERT vector_outbox (op='upsert', status='pending')
               COMMIT                        ◄── one durability boundary
                     │
  scheduler (30 s) ──┴──► embed (batched) ──► Chroma upsert ──► status='done'
```

Delivery is at-least-once, which is safe because upserts are idempotent. Four things make it
economical and observable:

- **Last-op collapse** — five edits between drains are one embed of the current state, not five.
- **`content_hash` skip** — editing a non-embedded field (price) costs zero API calls.
- **Fatal vs transient errors** — a 402/401/404 halts the drain instead of retrying each item five
  times; rows stay pending, uncharged, with an actionable message. 429/5xx still retry per item so
  one bad row can't block the queue.
- **`/admin/sync`** — active products vs chunks in Chroma vs queue depth, side by side, with a
  "Sync now" button.

Full reasoning in [design.md §7](documentation/design.md). Hybrid retrieval over this (Chroma + FTS5
fused by RRF) and the validate node are still _pending_ — see
[arch §3](SmartReco_Architecture_v2_FINAL.md).

---

## 5. Agent workflow

_Pending — two-LLM-call happy path, node graph, and LangSmith trace._

---

## 6. Event tracking

**Built and verified, both halves.** The requirement is that tracking must not slow down or break
the frontend, and three rules follow:

**Never block.** Events go into an in-memory array; the network happens on a timer, never on the
interaction. No handler awaits a POST. A page load makes **zero** network calls — verified.

**Never lose the last batch.** The most interesting event (a long dwell) happens exactly when the
tab closes and `fetch` gets killed, so unload flushes go through `sendBeacon`. Beacon can't set
headers, which is why the session rides the `sid` **cookie** — anything header-based would silently
lose precisely those events.

**Never break the page.** One try/catch-wrapped IIFE, every send fire-and-forget. An analytics bug
must not take the product down.

| Knob | Value | Why |
|---|---|---|
| batch | 10 events | flush early when the user is active |
| interval | 5 s | bound the loss window when they're not |
| queue cap | 200 | a runaway page can't exhaust memory |
| scroll sampling | 150 ms | scroll fires at refresh rate — sampling keeps it off the main thread |

**Throttling, concretely:** the scroll listener is `{passive: true}`, samples on a timer, and emits
only the *highest new* milestone of 25/50/75/100. Flicking top-to-bottom is **one** event, not four;
scrolling back up is none. Measured: 50 raw scroll events → 0 tracked events.

**Dwell** is time the page is *visible*, not wall-clock — a tab open overnight isn't 30,000 seconds
of interest. Under 1 s is a bounce and isn't recorded.

**Delivery:** a failed send is re-queued rather than dropped; every event carries a
`crypto.randomUUID()` and the writer uses `ON CONFLICT (event_uuid) DO NOTHING`, so retries and
beacon duplicates can't double-count.

Server side: `POST /api/events` returns **202** immediately and a background writer batches to
SQLite (200 rows or 1 s). Queue capped at 10,000 with a `DROPPED` counter at `/api/events/stats` —
backpressure is visible rather than silent.

Anonymous visitors are tracked by a `sid` cookie; on register or login their prior events are
stitched to their `user_id`, so a new account's first recommendation can draw on what the person did
before signing up.

Verified with 24 behavior assertions (tracker executed in Node against a DOM stub) plus a live
round-trip: the exact JSON `tracker.js` emits, POSTed to a running server and read back out of
SQLite. [design.md §8](documentation/design.md).

_p95 latency: pending._

---

## 6a. User profiles

**Built.** `/profile` collects what browsing history can't say: bio, goals, skills, years of
experience, and a resume (uploaded or pasted). On a brand-new account there is no behavior at all,
so this is the only signal available for a first recommendation.

Declared fields are kept strictly separate from the behaviorally-derived ones in the same table —
different trust, different lifetimes, and the profile router can never corrupt the interest model.
`.txt`/`.md` are parsed natively; PDFs are stored but **loudly** report that text wasn't extracted
rather than silently saving an empty profile. [design.md §9](documentation/design.md).

---

## 7. Bonus features

_Pending — scheduler/digest, observability, evaluation harness, and the fourth are designed in
[arch §8–§9](SmartReco_Architecture_v2_FINAL.md)._

---

## 8. Evaluation

_Pending — 7 personas, Coverage, Diversity@5, and an ablation. Every number will be labeled
measured or directional._

---

## 9. Setup & run

**Requirements:** Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt

cp .env.example .env            # then set MESH_API_KEY and SECRET_KEY
```

Create the schema, load the catalog, and mint an admin:

```bash
python -m app.db.init_db                             # tables + FTS5 + triggers + indexes
python -m app.catalog.sync_sql                       # data/data_1 → products (no API key needed)
python -m app.auth.cli create-admin you@example.com  Ex: app@kraishnaik.in# first admin
```

Run it:

```bash
uvicorn app.main:app --reload --workers 1     # → http://127.0.0.1:8000
# or 
make dev
```

Then register at `/auth/register` as a regular user, fill in `/profile`, browse the catalog, and log
in as the admin to manage products at `/admin/products`.

**With a Mesh API key**, additionally chunk and embed the catalog into Chroma:

```bash
python -m app.catalog.ingest data/data_1 --allow-pending    # chunk → embed → Chroma
python -m app.catalog.outbox                                # drain pending product writes
```

`settings.use_mesh` is false when `ENV=test` or no key is set, so tests and the browsable app run
entirely offline. Without a key the app is fully usable — products are still created and queued;
they simply wait in `vector_outbox` until a key exists, which `/admin/sync` shows plainly.

> **Note on `init_db`.** It creates missing tables *and* adds columns that exist in the models but
> not yet in the database, reporting each one. It is not a migration tool — renames, drops and type
> changes are not detected — but re-running it after a model change no longer silently does nothing.

> **If Chroma sync reports `402 spend_limit_exceeded`,** the Mesh account is out of balance.
> Embeddings are a paid call (listing models is not, so startup still reports all models available).
> Nothing is lost: the queue holds, and `/admin/sync` → *Sync now* completes the work after a top-up.

### Windows notes

`make` is not available on a default Windows install. Every target has a direct equivalent — the
table below is the full list; the `Makefile` is a convenience for Unix shells, not a requirement.

`python -m app.db.init_db` prints a `✓`, which crashes on the cp1252 console default. Either set
`$env:PYTHONIOENCODING="utf-8"` once per session, or ignore the traceback — the schema is created
before the print.

### Command reference

| Make target        | Direct command                                                  |
| ------------------ | --------------------------------------------------------------- |
| `make dev`       | `uvicorn app.main:app --reload --workers 1`                   |
| `make init`      | `python -m app.db.init_db`                                    |
| `make validate`  | `python data/data_1/validate_seed.py data/data_1`             |
| `make catalogue` | `python -m app.catalog.catalogue data/data_1`                 |
| `make ingest`    | `python -m app.catalog.ingest data/data_1 --allow-pending`    |
| `make test`      | `python -m pytest tests/ -q`                                  |
| `make lint`      | `ruff check app/ evals/ tests/`                               |
| —                 | `python -m app.catalog.sync_sql` (catalog → SQL, no API key) |
| —                 | `python -m app.catalog.outbox` (drain pending → Chroma)       |
| —                 | `python -m app.auth.cli create-admin EMAIL`                   |
| —                 | `python -m app.auth.cli make-admin EMAIL`                     |
| —                 | `node tests/tracker/test_tracker.js` (tracker.js behavior)    |

Note `make ingest` depends on `validate` and `catalogue`; run those first if you invoke the ingest
command directly.

Test dependencies live in the `dev` extra and are not installed by default:
`uv pip install pytest pytest-asyncio pytest-cov` (or `pip install -e ".[dev]"`).

---

## 10. Trade-offs & future work

**Taken deliberately, for a four-day build:**

- Server-rendered over React — the tracker needs real page loads, and a Node toolchain is a second
  runtime to deploy ([design.md §2](documentation/design.md)).
- SQLite over Postgres — one writer, many readers, WAL mode. Correct for this workload; the ceiling
  is real but far above a demo.
- `init_db` over a migration tool — greenfield app with a four-day life. It now reconciles added
  columns so a model change isn't silently ignored, but it is still not Alembic.
- No CSRF tokens (`SameSite=lax` covers the realistic threat), no login rate limiting, no password
  reset. All three are production requirements and none is an Aug 9 requirement.
- No PDF parsing library — a resume's value here is its text, and the paste box gets that with zero
  parsing risk. The extractor fails loudly rather than storing an empty profile.
- Browser tests run `tracker.js` under Node against a DOM stub rather than a real browser. It proves
  the batching, throttling and beacon *logic*; it does not prove cross-browser rendering.
- APScheduler in-process, `--workers 1` — two workers would drain the outbox twice concurrently.

**Future work:** planner-LLM analysis, knowledge graph, learned ranker, Redis Streams for the event
pipeline, collaborative filtering, A/B over Mesh fan-out. For the dual-write specifically: a
reconciliation sweep comparing `products.content_hash` against Chroma metadata, to catch drift from
writes that bypassed the outbox entirely.

---

## Repository layout

```
app/
  auth/         security (bcrypt, signed cookies) · routes · deps · admin CLI
  admin/        product CRUD, ingest trigger, vector-sync page, agent-run observability
  profiles/     declared user info — routes · resume intake/extraction
  catalog/      loader · chunker · freshness · ingest · sync_sql · browse routes
                vectors.py  → the Chroma write path
                outbox.py   → drains vector_outbox into Chroma (the dual-write)
  agent/        LangGraph nodes, Mesh client, scorer, triggers
  tracking/     event ingest queue + routes
  web/          templates, static (tracker.js), recommendations route
  db/           models · session (WAL pragmas) · init_db · seed
  scheduler/    APScheduler jobs — 30 s outbox drain, nightly maintenance
data/
  data_1/       hand-curated course catalog (12 courses, one JSON per course)
  resumes/      uploaded resumes, stored as user_{id}.{ext}  (gitignored)
  COURSE_SCHEMA.md
tests/
  test_profiles.py  test_outbox.py  test_freshness.py  test_smoke.py
  tracker/          test_tracker.js — runs tracker.js under Node
documentation/
  design.md     auth · roles · schema · dual-write · tracking · profiles
```
