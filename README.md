 

# SmartReco — Behavioral AI Recommendation Agent

A course-recommendation platform that watches what a user actually does — pages browsed, searches
run, time spent, cards clicked and dismissed — and turns that behavior into a small set of grounded,
persuasive recommendations.

The thesis in one sentence: **the expensive part (an LLM) runs only when cheap deterministic code
has established that behavior materially changed.**

> **Build status — 6 Aug 2026.** Built and verified: the platform foundation (web app, auth, roles,
> schema, catalog browsing, admin CRUD), **user profiles** (bio, goals, skills, resume),
> **product management with dual-write** to Chroma, **behavioral event tracking** end to end
> (browser → queue → SQLite), and the **email layer** (§7) — four personalized formats, an
> idempotent daily digest on the scheduler, and a credential-less fallback so it runs without
> secrets. The retrieval, ranking and agent layers are designed and partially implemented; sections
> marked _pending_ below will carry measured numbers once those land. No number in this README is
> estimated — anything not yet measured says so.

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

## 6a. Accounts and the declared profile

**Built.** Registration takes email and password plus four **optional** fields — name, target role,
years of experience, and a one-line goal. A new account has no behavior, so what someone tells us is
the only signal a first recommendation can use; asking while they are already filling in a form
beats hoping they visit `/profile` later. They stay optional because a required six-field signup is
an abandonment funnel, and everything is editable afterwards. A failed password rule re-renders
every value already typed.

`/profile` collects the rest: bio, skills, current role, education, links, and the constraints that
actually gate a recommendation — **budget, hours per week, preferred format**. Profile completeness
is shown as a weighted meter that **names the fields still missing**, because "62% complete" is a
nag and "add your target role and goals" is a prompt.

Declared fields are kept strictly separate from the behaviorally-derived ones in the same table —
different trust, different lifetimes, and the profile router can never corrupt the interest model.

---

## 6b. Resume intake and ATS scoring

**Built.** Upload a resume (PDF, `.docx`, `.txt`, `.md`) or paste the text; saving runs an ATS
check against your target role. PDF is parsed with `pypdf`, `.docx` by reading `word/document.xml`
straight out of the zip with the stdlib — no `python-docx` dependency. Extraction failure is
**loud**: a scanned PDF, a protected one, or a legacy `.doc` keeps the file, says exactly what
happened, and points at the paste box rather than saving an empty profile.

**The scorer is pure Python — no LLM call.** A model asked to rate a resume out of 100 returns a
plausible number that moves between runs on identical input. A user who edits their resume and
re-runs must be able to trust that 61 → 74 means the resume improved. Reproducibility is the
feature, and it is testable in a way an LLM scorer is not:

```
ats_score = 0.45·keyword + 0.20·structure + 0.25·experience + 0.10·readability
```

Eight target roles, each with `must` skills weighted double `nice` ones. Two details that separate
this from a keyword counter: **word-boundary matching** (substring matching makes `"R"` match every
word containing it) and an **alias table**, so "torch" satisfies PyTorch and "k8s" satisfies
Kubernetes. False gaps are worse than missed ones — they send someone to buy a course teaching what
they already know — and a test asserts every reported gap really is absent from the text.

**The output that matters is the gap list, not the score.** `missing_skills` becomes the retrieval
query that drives both the advisor and the recommendations; a score with no gaps would be a vanity
metric. Runs are stored with `is_current`, so re-scoring against a different role keeps the history
that makes the number mean something. [arch §13.2](SmartReco_Architecture_v2_FINAL.md).

---

## 6c. The career advisor (chat)

**Built.** A chat panel on every page for signed-in users, grounded in the same catalog as the
recommendation cards.

**It cannot invent a course.** Hybrid retrieval (Chroma + FTS5, fused by RRF) names the only courses
the model may mention, and the answer is checked against that set afterwards — the chat equivalent
of the validate node. A hallucinated id is stripped *along with the course title in front of it*,
because removing only the marker leaves an invented name in the prose as plain text: the same false
claim with the link taken off.

It reasons over your ATS gaps first, then your declared profile, then your behavioral interest
vector. A stated budget is applied as a **SQL filter**, not as a polite request in the prompt.
Retrieval degrades in a stated order: no key or no Chroma drops it to FTS5-only and logs which path
ran, rather than failing the turn.

Cost is bounded by shape, not by luck: history is capped at 8 turns and retrieval at 6 courses, so
**the cost of turn N does not depend on N**. [arch §13.3](SmartReco_Architecture_v2_FINAL.md).

---

## 7. Email — scheduler, digest, and the four formats

Four message kinds share one transport, one Jinja environment, and one opt-out switch.
[`app/mail/`](app/mail/). Design detail: [design.md §10](documentation/design.md).

| Kind | Trigger | Won't send unless | Respects opt-out |
| --- | --- | --- | --- |
| **Daily digest** | Cron at `DIGEST_HOUR` (default 16:00) | there's a current recommendation set | yes |
| **Welcome** | Registration, fire-and-forget | — always renders | no — transactional |
| **ATS report** | User presses "Email me this report" | a resume analysis exists | no — explicitly requested |
| **Re-engagement** | Cron, Mondays 10:00 | the user is idle `REENGAGE_AFTER_DAYS`+ and we can name a course they opened | yes |

### When mail goes out

The digest is the only one on a daily clock, and it is **idempotent per user per day** — the
`digest_log` unique constraint on `(user_id, sent_date)` is what makes that true. A restart at
16:01, an admin pressing the manual trigger, and the cron itself all converge on one email. Run
the trigger twice and the second run reports everyone as `skipped`; that skip *is* the guarantee.

Two rules keep the volume honest:

- **No content, no send.** A user with no current recommendation gets nothing rather than an empty
  "no picks today" mail. The whole planner design (§3) is that a recommendation exists only when
  behavior justified generating one — so silence is the correct output, not a failure.
- **A nudge must name something specific.** Re-engagement sends only when it can point at the
  course the person actually last opened. Without that it's a "we miss you" mail, which is the
  genre people mark as spam.

The ATS report is behind a button rather than fired on every analysis: `save_profile` re-scores on
every save, and mailing a report each time someone fixes a typo would get the sender filtered
within a week.

### How mail is sent

`aiosmtplib` over STARTTLS (port 587) or implicit TLS (465), chosen by `SMTP_PORT`. Set the four
`SMTP_*` vars in `.env` and mail is delivered for real.

**With no credentials configured, sending still works** — the rendered message is written to
`data/outbox_mail/*.eml` (openable in any mail client) and reported as `stored` instead of `sent`.
This is a supported mode, not an error path: tests, CI, and a laptop demo all run with no secret,
and an expired password degrades to "the digest is on disk" rather than a stack trace inside a
scheduler job at 16:00. `SendResult.mode` distinguishes the two everywhere, so nothing in the UI
ever claims a delivery that did not happen.

Failures return, they never raise. The digest loops over users; one bad address must not abort the
other forty-nine.

### What "personalized" means here

Every format is assembled per user from stored state — none of it is a mail-merge over a template
with a name slotted in:

- The digest carries the agent's own **narrative**, plus each item's **hook** and **reason** — the
  per-user text that already justifies the card on `/recommendations`.
- It closes by naming the **resume gaps** those courses were chosen against, which is the seam
  between the ATS feature and the recommender: _"Chosen partly against the gaps in your resume for
  Generative AI Engineer: langgraph, rag, evaluation."_
- The welcome mail lists the **specific profile fields this account is missing**, weight-ordered by
  `completeness()` — the same three the progress meter asks for.
- Re-engagement names the **last course actually opened** and how long ago.
- Missing a name degrades to no name, never to `Hi meetsupriyoc...` — a mangled email local-part
  advertises that the sender knows nothing about you.

Every message ships HTML **and** hand-written plain text. The text half is not a formality: it's
what screen readers and spam filters read, and auto-degrading HTML loses every link.

### Operating it

`/admin/mail` shows transport state first — the most common confusion is "I pressed send and
nothing arrived" when SMTP was unconfigured and the `.eml` is exactly where it should be. From
there: send any kind to any user, preview any kind in the browser without sending, and run the
digest fan-out on demand (forced or idempotent).

`/profile` carries the single opt-out switch every automated email links to.

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
| —                 | `python -m app.mail.cli send digest EMAIL` (one message, now)  |
| —                 | `python -m app.mail.cli run-digest [--force]` (the 16:00 job)  |
| —                 | `python -m app.mail.cli check` (SMTP reachability + auth)      |
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
                ats.py      → deterministic ATS scoring + gap analysis
  chat/         the career advisor
                retrieval.py → hybrid Chroma+FTS5 retrieval, RRF-fused
                agent.py     → context assembly, prompt, grounding enforcement
  catalog/      loader · chunker · freshness · ingest · sync_sql · browse routes
                vectors.py  → the Chroma write path
                outbox.py   → drains vector_outbox into Chroma (the dual-write)
  agent/        LangGraph nodes, Mesh client, scorer, triggers
  mail/         outbound email
                sender.py     → SMTP transport + store-to-disk fallback
                messages.py   → the four kinds: digest · welcome · ATS · re-engage
                digest.py     → the daily fan-out, idempotent via digest_log
                templating.py → Jinja env for email (inline CSS, absolute URLs)
                preview.py    → render without sending, for /admin/mail
                templates/    → *.html + *.txt, one pair per kind
  tracking/     event ingest queue + routes
  web/          templates, static (tracker.js · chat.js · ui.js), recommendations
  db/           models · session (WAL pragmas) · init_db · seed
  scheduler/    APScheduler jobs — 30 s outbox drain, nightly maintenance,
                16:00 digest, Monday re-engagement sweep
data/
  data_1/       hand-curated course catalog (12 courses, one JSON per course)
  resumes/      uploaded resumes, stored as user_{id}.{ext}  (gitignored)
  outbox_mail/  rendered .eml files when SMTP is unconfigured  (gitignored)
  COURSE_SCHEMA.md
tests/
  test_ats.py       test_chat.py      test_profiles.py   test_mail.py
  test_outbox.py    test_freshness.py test_smoke.py
  tracker/          test_tracker.js — runs tracker.js under Node
documentation/
  design.md     auth · roles · schema · dual-write · tracking · profiles
```

**Tests: 121 passing** (`python -m pytest tests/ -q`), plus 24 tracker assertions under Node.
