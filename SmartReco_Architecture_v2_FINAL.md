# SmartReco — Architecture v2 (FINAL, APPROVED)

**Status:** ✅ Approved for build. This supersedes the v1 plan (Opus, 3 Aug).
**Today:** 5 Aug 2026 · **Deadline:** 9 Aug 2026 12:00 IST · **Remaining:** ~4 working days
**Stack (unchanged):** FastAPI · SQLite (WAL + FTS5) · Chroma · LangGraph · APScheduler · LangSmith · Jinja2 · Mesh API

---

## 0. What changed and why — the decision log

Three critiques were reconciled. The governing principle: **Grok's calendar critique wins every tie.** Anything that doesn't survive contact with a 4-day window is either simplified, flagged behind config, or moved to README future-work.

### Adopted (goes into code)

| # | Change | Source | Cost | Why |
|---|--------|--------|------|-----|
| A1 | SQLite PRAGMA via SQLAlchemy connect-event listener (WAL, synchronous=NORMAL, cache, temp_store) | DeepSeek 1.1 | 15 min | v1 said "WAL mode" but never specified *how* with aiosqlite; this is the correct mechanism. Also add `wal_checkpoint(TRUNCATE)` to the nightly job. |
| A2 | Structured-output fallback chain: `gemini-2.5-flash` → `gpt-4o-mini` on parse failure | DeepSeek 2.1 | 30 min | v1's trap list *knew* `response_format` fails silently but had no runtime fallback. This is the single highest-probability catastrophic failure. P0. |
| A3 | **Deterministic-first grading.** Pure-Python coverage check (do top-5 candidates cover the inferred categories?). LLM grade + refine loop runs **only** when deterministic coverage is ambiguous (0 < ratio < 1). Loop cap: **1** (was 2). | DeepSeek 2.2 + Grok #2, reconciled | 45 min | Happy path drops from 3 LLM calls to 2 (analyze + generate). Grok wanted the loop cut; DeepSeek wanted it guarded. Deterministic-first does both: the loop still exists for the README/demo story but almost never fires, and can never fire on hallucinated insufficiency. |
| A4 | SQLite **FTS5** virtual table + triggers, hybrid retrieval = vector + FTS via RRF | DeepSeek 1.3 | 1 h | Cheap, catches exact-title/category queries semantic search misses, and honestly earns the word "hybrid" in the README. Replaces the fuzzier v1 "multi-query only" story. |
| A5 | **Batch embeddings** in the outbox drain (one Mesh call per drain batch, not per product) | DeepSeek 2.4 | 30 min | Seeding 60 products = 1–2 API calls instead of 60. Also makes the seed step in setup fast. |
| A6 | Outbox indexes: `(status, created_at)` + partial on `attempts` | DeepSeek 1.2 | 5 min | Free. |
| A7 | **Dual-horizon memory**: run the interest scorer twice — λ_long = ln2/72h (enduring interests) and λ_short = ln2/6h (current session intent) — merge as `0.6·long + 0.4·short`. Fingerprint over the merged vector; expose both vectors to the generate prompt ("long-running interest in X, today focused on Y"). | ChatGPT (long/short-term memory), implemented deterministically | 30 min | This is ChatGPT's best idea at 1/100th the cost it proposed. Two decay constants ≈ two memories. The generate node can now write copy like "you've been circling agentic AI for a week, and this morning you went deep on LangGraph" — directly serving the persuasion requirement. |
| A8 | `conversion` event type + high scorer weight (10.0) + `rec_click` / `rec_dismiss` feedback events that feed back into the scorer (dismiss = negative weight −3 on that product's category) | DeepSeek 7.2 + ChatGPT feedback loop | 45 min | Closes the loop cheaply. The scorer *is* the interest model; feedback events updating it *is* the feedback loop. No new ML needed. |
| A9 | **Recommendation cards** with per-item: `reason` (behavior-tied), `confidence` (deterministic: normalized fusion-rank score × interest-match, NOT an LLM number), and `next_step` (from A10 graph edges) | ChatGPT (confidence + explainability) + DeepSeek 7.1 | 1 h | Judges see explanation without a separate `/explain` endpoint (rejected, see R6). Confidence is honest because it's computed, not hallucinated. |
| A10 | **Lightweight product graph in seed data**: `prereq_ids` and `related_ids` fields per product in `seed/products.json`. Used for (a) the card's "next step" suggestion, (b) a +boost in fusion ranking for products adjacent to already-viewed ones. | ChatGPT (knowledge graph), scoped to 1 hour | 1 h | The full KG is scope inflation; two ID-list columns in the seed JSON are not. Gives the README a "graph-aware retrieval" line that is *true*. |
| A11 | Cookie flags (`HttpOnly`, `SameSite=lax`, `Secure` in prod), no Authorization header in logs | DeepSeek 6.1/6.3 | 15 min | Free correctness. |
| A12 | `GET /api/events/stats` (queue depth, processed today, dropped) | DeepSeek 3.1 | 20 min | Judges testing with scripts get visibility; costs nothing. |
| A13 | Scroll milestone fix: check every 150 ms, emit the **highest** milestone crossed (not first-match on throttled callback) | DeepSeek 3.2 | 10 min | v1's 250 ms throttle could skip 50% on fast scrolls. |
| A14 | **Default rerank = deterministic weighted fusion** (RRF rank + interest-category match + rating prior + graph adjacency boost). `RERANK_MODE ∈ {fusion, cross_encoder, llm}` config flag; `fusion` is the submission default. | Grok #3 + engineering judgment | saves ~2 GB of torch deps + a failure mode | Kills the local cross-encoder from the critical path: no Mesh-purity optics question, no sentence-transformers install in setup, no cold-start model download. Cross-encoder stays available behind the flag and gets one ablation row *if* time allows. |
| A15 | Persona diversity in evals: add `confused`, `price_sensitive`, `two_interests` personas | DeepSeek 5.1 | 30 min | The `two_interests` persona directly validates the multi-query + RRF design (blended queries would fail it). |
| A16 | Nightly cleanup job: orphaned anonymous events > 90 days; WAL checkpoint | DeepSeek 3.3/1.1 | 10 min | Folded into the existing 03:00 reconcile job. |

### Rejected (goes into README "future work", not into code)

| # | Proposal | Source | Why rejected |
|---|----------|--------|--------------|
| R1 | LLM Planner Agent deciding retrieve/clarify/memory/similar-users | ChatGPT | **The trigger policy is the planner, and it being deterministic is the thesis.** An LLM planner adds a call to every run and re-introduces exactly the waste the design exists to eliminate. The README gets a paragraph saying precisely this — it converts the "not truly agentic" criticism into the submission's strongest argument. |
| R2 | Full knowledge graph (prereq chains, bundle edges, graph retrieval) | ChatGPT | A10 captures 80% of the demo value at 5% of the cost. Neo4j/networkx layers in 4 days is how submissions die. |
| R3 | LightGBM ranker + feature engineering | ChatGPT | Needs training data that doesn't exist (no real users). A model trained on synthetic personas would be theater. Fusion ranking (A14) with explicit, documented weights is more honest and more explainable. Future work. |
| R4 | Kafka / Redis Streams / feature store | ChatGPT | The asyncio queue *is* the stream for this scale. README sentence: "swap the in-process queue for Redis Streams at multi-instance scale." |
| R5 | Collaborative filtering / similar-user retrieval | ChatGPT | Cold-start platform with 4 seeded personas — there are no similar users. Future work, honestly labeled. |
| R6 | Separate `POST /recommendations/{id}/explain` endpoint that re-calls the LLM | DeepSeek 7.1 | An extra LLM call per view contradicts the efficiency thesis. Explanations are generated *once* inside `generate` and stored on the card (A9). Same judge-visible outcome, zero marginal tokens. |
| R7 | Category-similarity collapse in the fingerprint | DeepSeek 2.3 | Valid concern, wrong fix for the window: a hand-tuned similarity matrix is another artifact to debug. Mitigation instead: fingerprint over the **merged dual-horizon vector** (A7), which is naturally smoother, and keep the cosine-distance trigger (>0.15) as the primary signal with the bucket-hash as cache key only. Documented as a known trade-off. |
| R8 | 5-seed statistical ablations (mean ± std) | DeepSeek 5.2 | Right in principle; at 4 days, one clean run per config with the caveat "single run, directional" written honestly beats fabricated rigor. **Metrics must be measured or labeled — never invented** (consistent with the SENTINEL sourced-or-labeled rule). |
| R9 | PR-trigger changes / retry loops in the CI workflow | DeepSeek 4.x | The workflow file is platform-supplied and freshly re-issued; the platform validates runs server-side. Don't edit what the organizer controls. (Separate note: run it with a throwaway, spend-capped Mesh key — it executes remote code with your secrets in env.) |
| R10 | Rate-limit middleware, PII masking, prompt-injection detection, secrets manager | ChatGPT/DeepSeek | Admin routes already role-gated; the rest is a README security paragraph, not code, at this scale. |

### Cut-list re-ordering (Grok's calendar critique, accepted in full)

The v1 day-by-day was a 10–14 day plan. v2 re-plans from **today** (schedule in §8). Standing rule: **the digest cron + SMTP is the first thing cut** — `POST /admin/trigger-digest` rendering the HTML email is enough for the video. The four never-cut items stand: dual-write sync, non-blocking tracking, validate node, trigger policy.

---

## 1. System architecture v2 (delta view)

Unchanged from v1 unless marked ▲.

```
BROWSER
  tracker.js — batch(10 | 5s | sendBeacon) · scroll milestones ▲A13
             · rec_click / rec_dismiss / conversion events ▲A8
        │
        ▼
FASTAPI
  POST /api/events → 202 → asyncio.Queue → executemany writer
  GET  /api/events/stats ▲A12
  Auth (cookie: HttpOnly/SameSite/Secure ▲A11)
  Admin CRUD → product_service.upsert() → SQLite ──► vector_outbox
                                    │                    │ (batched embeds ▲A5)
                                    └─► FTS5 triggers ▲A4 └──► CHROMA (Mesh embeddings)
  GET /recommendations → RecommendationService
        │
   interest_scorer (dual-horizon λ72h + λ6h ▲A7, feedback-aware ▲A8)
        │ fingerprint unchanged → cached card (0 LLM calls)
        │ changed / trigger fired
        ▼
   LANGGRAPH AGENT (2-LLM happy path ▲A3) → LangSmith → Mesh
        ▼
   recommendations table → recommendation cards ▲A9

APSCHEDULER (single worker, SCHEDULER_ENABLED flag)
  30s   drain outbox (batch embed ▲A5)
  15m   refresh stale recs (active users only)
  03:00 reconcile + WAL checkpoint + orphan cleanup ▲A16
  16:00 digest  ← BONUS TIER, first cut; manual trigger endpoint ships regardless
```

## 2. Schema deltas

Everything from v1 §2 stands. Additions:

```sql
-- ▲A4  full-text search, kept in sync by triggers
CREATE VIRTUAL TABLE products_fts USING fts5(
  title, description, category, tags, content=products, content_rowid=id);
-- + AFTER INSERT / UPDATE / DELETE triggers (see setup.sh)

-- ▲A6
CREATE INDEX idx_outbox_status_created ON vector_outbox(status, created_at);

-- ▲A8  new event types (no schema change — event_type values):
--   'conversion' (weight 10.0), 'rec_click' (+4 on category),
--   'rec_dismiss' (−3 on that product's category)

-- ▲A9  recommendations.items JSON items gain:
--   {product_id, reason, hook, confidence, next_step_id}

-- ▲A10  products gain: prereq_ids JSON, related_ids JSON  (from seed data)

-- ▲A7  user_profiles gains: interests_short JSON (long-horizon stays in interests)
```

## 3. The agent v2 — two LLM calls on the happy path

```
load_state
    │
analyze_behavior            ← LLM #1 (fast model, json_schema, fallback chain ▲A2)
    │                          input: merged interests + dual-horizon summary ▲A7
route_signal ─ insufficient ─► cold_start → persist
    │
retrieve                    ← per-interest Chroma query + FTS5 query, RRF ▲A4
    │
fusion_rank                 ← ▲A14 deterministic: RRF + interest match
    │                          + rating prior + graph adjacency ▲A10 → top 8
deterministic_grade         ← ▲A3 pure Python coverage check
    │
    ├─ ratio == 1.0 ──────────────────────► generate
    ├─ ratio == 0.0 ─► refine_query (1 loop max) ─► retrieve
    └─ 0 < ratio < 1 ─► llm_grade (LLM #2b, rare) ─► generate | refine
    │
generate                    ← LLM #2 (writer model): narrative referencing BOTH
    │                          horizons ("all week… and this morning…") ▲A7
    │                          + per-item reason/hook; confidence computed
    │                          in Python from fusion scores, attached after ▲A9
validate                    ← unchanged: IDs ⊆ retrieved set, active, ≤5,
    │                          deduped, reasons ≤220 chars. The grounding guarantee.
persist → card
```

**README framing for the "not truly agentic" critique (use this paragraph):**
> The planner in this system is deliberately not an LLM. The trigger policy — fingerprint delta, event thresholds, high-intent signals, debounce, in-flight coalescing — is a deterministic planner that decides *whether reasoning is worth paying for*. Systems that put an LLM in the planning seat pay tokens to decide whether to pay tokens. Ours plans in microseconds and reasons only when behavior has materially changed: N× fewer LLM calls at equal recommendation quality (see ablation).

## 4. Mesh integration deltas

v1 §5.5 stands (ChatOpenAI → Mesh, embeddings via Mesh, MeshClient retry/backoff). Additions:

- ▲A2 `structured_call(messages, schema)`: try `MODEL_FAST` with `response_format`; on JSON parse failure or missing fields, one retry with `MODEL_FAST_FALLBACK=openai/gpt-4o-mini`. Log which path fired into `agent_runs`.
- ▲A5 `embed_batch(texts: list[str])` — single POST; outbox drain and seed both use it.
- Confirm structured-output support per model via `GET /v1/models` **at startup**, log the result; don't assume.

## 5. Tracking deltas

v1 §4 stands. Additions: A13 scroll fix, A8 new event types wired into the rec card UI (`rec_click` fires on card CTA, `rec_dismiss` on the card's ✕), A12 stats endpoint.

## 6. Recommendation card (what the user and the judge see) ▲A9

```
┌─ Agentic AI Bootcamp ────────────────────────── 92% match ─┐
│ "You've been circling agentic architectures all week —    │
│  and this morning you went deep on LangGraph twice."      │
│  ✓ 3 product views in Agentic AI   ✓ searched 'langgraph' │
│  ✓ fits your ₹ band                ✓ intermediate level   │
│  Next step: Advanced LangGraph Patterns →                 │
│                                    [View course]  [✕]     │
└───────────────────────────────────────────────────────────┘
```
Confidence = `0.5·norm(fusion_score) + 0.35·interest_match + 0.15·level_match` — computed, displayed, and documented. Never asked of the LLM.

## 7. Evaluation v2

v1 metric table stands. Personas: the original four **plus** `confused`, `price_sensitive`, `two_interests` (▲A15). Ablation rows (single run each, labeled as directional — R8): trigger-policy off/on · fusion vs raw-RRF · grade-path off/on. Sourced-or-labeled rule applies to every number in the README.

## 8. Re-planned schedule (from tonight, 5 Aug)

| When | Work | Green means |
|---|---|---|
| **Aug 5 (rest of today)** | Repo + CI + secrets pushed (if not done). setup.sh run. Skeleton: config, models (+FTS5, pragmas listener), auth, admin CRUD, `product_service` dual-write with **simple synchronous embed-on-upsert** (outbox deferred), seed 60 courses via batched embeds. | Add product in admin → found by Chroma similarity search. CI green. |
| **Aug 6** | tracker.js (with A13, A8 events) + `/api/events` queue/writer + stats + identity stitching + browse/search/product pages. Then: scorer (dual-horizon) + fingerprint + trigger policy + rec cache. | Browse → events land, p95 measured. Fingerprint changes with behavior. |
| **Aug 7** | LangGraph v2: analyze → retrieve (hybrid) → fusion_rank → det_grade → generate → validate. Mesh fallback chain. Rec cards on the site. **Go/no-go checkpoint at EOD:** core loop demo-able end to end or bonuses get cut now. | Behavior visibly drives grounded, persuasive cards. LangSmith traces. |
| **Aug 8 AM** | Upgrade embed-on-upsert → transactional outbox + drain (if AM is calm; else document sync as "synchronous dual-write, outbox as future work" — still honest dual-write). `agent_runs` admin page. | Outbox drains or fallback documented. |
| **Aug 8 PM** | Eval harness (7 personas) + ablation + README (lead with efficiency numbers) + demo video (DeepSeek's 60s script is good — use it). Digest: manual trigger + rendered email; cron only if everything else is green. | README numbers are real. Video recorded. |
| **Aug 9 by 10:00** | Buffer, optional deploy, final push. | Submitted 2h early. |

**Cut order under pressure:** digest cron → outbox upgrade (keep sync dual-write) → cross-encoder ablation row → agent_runs page → deploy. **Never cut:** dual-write (either form), non-blocking tracking, validate node, trigger policy, measured efficiency numbers.

## 9. README future-work section (converts rejections into credibility)

One paragraph each, honestly labeled as not implemented: LLM planner trade-off analysis (R1) · full prerequisite knowledge graph (R2) · learned ranker once real interaction data exists (R3) · Redis Streams / feature store at multi-instance scale (R4) · collaborative filtering post cold-start (R5) · A/B framework over Mesh multi-model fan-out · statistical eval rigor (R8).

---

# Addendum v2.1 — round-2 critique adjudication (5 Aug, late)

Both critics approved v2. This addendum resolves their residual points. Same rule: the calendar wins ties. Total added build cost of everything adopted below: **≈ 2.5 h**.

## Adopted (patched into setup.sh where foundational)

| # | Change | Source | Disposition |
|---|--------|--------|-------------|
| B1 | **Fence-stripping JSON parser** in `mesh.py`: before `json.loads`, trim ```` ```json ```` fences and extract the outermost `{…}`. Applied to fast, fallback, and writer paths. | DeepSeek W1 | ✅ in setup.sh. Covers the case where *neither* model honors `response_format` — the last line of defense under the fallback chain. |
| B2 | **Popularity term in fusion**: `0.45·norm(rrf) + 0.3·interest_match + 0.1·rating + 0.1·popularity(log views from events) + 0.05·graph_adjacency`. | GPT #4 | ✅ nearly free — popularity is a GROUP BY over `events`, not a "tool". |
| B3 | **Diversity cap**: final selection allows ≤3 of 5 items from one category (simple cap, not MMR — same effect at this K, zero tuning). | GPT #9 | ✅ directly protects the `two_interests` persona. |
| B4 | **Recency penalty, not exclusion**: items shown in the user's previous current rec get a −0.15 fusion penalty; items with a `conversion` event are **excluded outright** (you don't re-sell a bought course). | GPT #6/#10 | ✅ penalty (not hard 7-day exclusion) because a 60-product catalog starves under exclusion. |
| B5 | `cos_dist` column on `agent_runs` + log `trigger_reason`/`cache_hit` per run — backs the 0.15 threshold with a measured distribution in the README. | GPT #11 + DeepSeek W3 | ✅ in setup.sh. |
| B6 | **Seed graph validation**: `seed.py` verifies every `prereq_ids`/`related_ids` slug exists before writing; fails loudly. | DeepSeek minor | ✅ in setup.sh. |
| B7 | **Chunked outbox embedding**: drain in chunks of 20; a failed chunk retries per-item, `attempts`+backoff absorb stragglers. | DeepSeek W2 | ✅ documented in the outbox TODO (built Aug 8). |
| B8 | Eval adds **Coverage** (% of catalog ever recommended across personas) and **Diversity@5** (distinct categories per rec set). Serendipity/novelty skipped — they need baselines that don't exist yet. | GPT #12 | ✅ two trivial computations. |
| B9 | Dev-ex: **Makefile** (`dev/seed/test/lint`), **pytest smoke tests** (`/healthz`, event ingest 202), **ruff** via `requirements-dev.txt`, `/health` alias. | GPT setup review + DeepSeek minor | ✅ in setup.sh. Docker, Alembic, pre-commit, black, mypy: rejected below. |

## Rejected (with the arguments — several go in the README)

| # | Proposal | Source | Why |
|---|----------|--------|-----|
| C1 | Conversation memory / rolling chat summary | GPT #1 | **There is no conversation in this product.** The brief specifies browse/search behavior → recommendations; users never chat with the agent. This solves a problem the system doesn't have. If a chat surface were added later, the dual-horizon scorer's short vector is where session intent already lives. |
| C2 | Rule-based dynamic planner step | GPT #2 | Already exists — it's `route_signal` + the trigger policy + the deterministic grade router. GPT's own example (`intent == new topic → retrieve, else cached`) *is* the fingerprint check. Disposition: README wording, zero code. |
| C3 | Tool layer (inventory/price/review/trending "tools") | GPT #3, echoed in closing | These are columns, not tools. Wrapping `products.price` in a "Price Tool" so the generator can "reason over tool outputs" adds latency, failure modes, and tokens to retrieve data the retrieval step already returns. Multi-tool orchestration goes in future work as an honest trade-off note — the README paragraph from §3 already argues why deterministic beats LLM-mediated here. |
| C4 | Extra graph arrays (`similar_ids`, `alternative_ids`, `bundle_ids`) | GPT #5 | `related_ids` already carries similar/alternative semantics at this catalog size; three more arrays = three more things to hand-curate in seed data by tomorrow. |
| C5 | Decay negative feedback | GPT #7 | **Already the case.** Every weight in the scorer — including `rec_dismiss: −3` — passes through `exp(−λ·age)`. A two-month-old dismissal is already ≈0. The critique missed that negatives ride the same decay as positives. README gets one sentence so judges don't miss it too. |
| C6 | Multiplicative confidence | GPT #8 | Multiplying five [0,1] terms collapses everything toward 0 (0.8⁵ ≈ 0.33 reads as "low" for a great match) and any single noisy term nukes the score. Weighted additive with documented weights is more stable and more explainable. Keep. |
| C7 | Docker + compose, Alembic, pre-commit/black/mypy | GPT setup | Greenfield SQLite app with a 4-day window: `init_db.py` *is* the migration story, and the judge runs `setup.sh`, not `docker compose`. Each is one README sentence under future work. ruff alone gives 80% of the lint value. |
| C8 | Redis-durable event queue | DeepSeek W6 | Agreed it's dev-grade; that's the design. README sentence: "swap `asyncio.Queue` for Redis Streams for durability across restarts at multi-instance scale" (already the R4 note). |

## Confirmations (no action)

- Queue overflow protection already exists: `maxsize=10_000`, `put_nowait` + `DROPPED` counter surfaced at `/api/events/stats` (DeepSeek W6-adjacent).
- CI workflow untouched; throwaway spend-capped Mesh key stands (both critics now agree).
- DeepSeek's fallback plan is adopted as official: **if LangGraph slips on Aug 7, ship scorer + fusion + single writer-LLM call for the narrative.** That is still a behavior-driven, grounded, efficient system — the graph is presentation, the policy is the product.

*Adjudicated and frozen 5 Aug 2026 (v2.1). No further architecture rounds — every remaining hour goes to code.*
