# SmartReco Course JSON Standard — v1.0

**Status:** frozen 6 Aug 2026. One file per course, filename = `<slug>.json`, in `data/data_1/`.
**Enforced by:** `data/course.schema.json` (structure) + `data/data_1/validate_seed.py` (curation rules
a schema can't express). Run the validator after **every** course you save, before `make ingest`.

This standard exists because the catalog is hand-curated from marketing pages, and marketing pages
lie by omission. The rules below are the ones that already caught real defects in the first two
courses (an objectives/syllabus mismatch, a coupon-driven price that would have been stale within
days, a 27-vs-32 module version skew). Follow them literally.

---

## 0. The three tiers — why fields are grouped the way they are

Every field belongs to exactly one tier. The tier decides where the field is allowed to travel.

| Tier | Where it goes | Fields |
|---|---|---|
| **T1 — Filter** | SQL columns + Chroma metadata. Cheap, exact, pre-retrieval. | `slug` `category` `level` `price` `price_band` `is_free` `is_active` `format.mode` `enrollment_status` |
| **T2 — Embed** | Chroma chunk *text*. This is what semantic search actually matches. | `title` `overview` `objectives` `module_groups` `projects` `skills` |
| **T3 — Persuade** | Injected verbatim into the `generate` prompt at answer time. **Never embedded.** | `price` `format` `mentors` `perks` `cohort_start` `career_roles` `rating` |

**The hard rule:** a T3 field must never appear in embedding text. Embedding "₹12000, Sat-Sun 8pm"
makes every course match every price query and every schedule query, which is how a catalog's
retrieval quality quietly dies. `NEVER_EMBED_KEYS` in the validator enforces this.

---

## 1. Required fields

Nine fields; a course without all nine is not ingestible.

```
slug  title  overview  category  level  is_free  skills  objectives  source_url
```

### `slug` — string, kebab-case, unique, == filename stem
The stable identity. Ladder edges (`prereq_ids` / `related_ids`) reference it, so renaming a slug
after ingest silently breaks other courses' edges. Pick it once.

### `title` — string
Verbatim from the source. This is the one field you copy exactly, because it is the course's name.

### `overview` — string, 2–4 sentences
**PARAPHRASED, always.** Never paste sponsor/Udemy marketing prose into the repo. Facts (price,
module count, duration, format) are used as-is; *prose* is rewritten. Every existing file carries a
`_comment_overview` recording this — keep the habit.

### `category` — string, one of the canonical set
```
Agentic AI · GenAI / LLM Engineering · Data Science · Data Engineering
MLOps / LLMOps · NLP · Computer Vision · Analytics · Cloud
```
One primary only. Everything else goes in `secondary_categories`. The diversity cap (arch B3,
≤3 of 5 recommendations from one category) counts `category`, not the secondaries — so putting a
course in the wrong primary bucket distorts the whole rec set, not just that course.

### `level` — `beginner` | `intermediate` | `advanced`
The **fusion-match anchor** — `confidence` reads it (`0.15·level_match`). Exactly one value.
For a course that genuinely spans the range (Python syntax → multi-agent MCP), set `level` to the
*primary audience* and add `level_range: [min, max]` alongside. `level_range` is stored for a
future two-sided match term; it is **not** scored today, so `level` must still stand alone.

### `is_free` — boolean
Independent of `price`. A course can have `price: 0` and `is_free: true`, or `price: null` with
`is_free: false` (Udemy coupon pricing). Never infer one from the other.

### `skills` — array of strings, 8–25 typical
Concrete named technologies and techniques, not adjectives. `"LangGraph"`, `"AWS Bedrock"`,
`"Corrective RAG"` — not `"modern AI"`. These feed both the overview chunk and the
`SKILLS OVERLAPPING USER INTEREST` line in the generate context, so precision here is directly
visible in recommendation copy.

### `objectives` — array of strings, ≥2 (target 4–8)
**The highest-value retrieval keys in the whole schema.** They're phrased the way users phrase
intent, so they out-match every other chunk type. Each objective gets its own Chroma chunk.

> **The corroboration rule.** An objective may only be kept if a real module in `module_groups`
> supports it. The published objectives block for the cloud bootcamp advertised SpaCy, HuggingFace,
> NER and sentiment analysis — none of which appear anywhere in its 28-module syllabus. Those were
> dropped into `objectives_dropped` and are not embedded. This matters because the `validate` node
> cites objectives back to the user as reasons; an uncorroborated objective is a lie with a
> citation attached.

### `source_url` — string, URL
Where a human can verify every fact in the file. If the syllabus came from a different document
than the landing page, add `source_urls: [...]` with both, and say which is authoritative in
`_comment_sources`.

---

## 2. Pricing

```jsonc
"price": 12000,          // numeric in INR, or null
"currency": "INR",
"price_band": "high",    // free | low | mid | high
"is_free": false,
"price_note": "..."      // REQUIRED when price is null
```

**Bands are defined, not vibes** (this standard exists partly because the first two files disagreed):

| band | INR |
|---|---|
| `free` | 0 |
| `low` | 1 – 2,999 |
| `mid` | 3,000 – 9,999 |
| `high` | ≥ 10,000 |

**`price: null` is legitimate and sometimes mandatory.** Udemy prices are coupon-driven and move
weekly; storing `4500` there guarantees the generate node states a wrong price as fact within days.
When price is volatile or gated behind a checkout, set `price: null` + a `price_note` the generator
can quote instead ("frequently discounted — check current price at checkout"), and set `price_band`
from the platform's typical range with the basis recorded in `_comment_price`.

Null price is **excluded from price-bounded retrieval filters** by design (a course whose price we
don't know cannot satisfy "under ₹5000"). `price_band` remains present in metadata, so band-level
matching still works.

---

## 3. Delivery & freshness — the `format` block

This block is what §6's scoring reads. It is the newest part of the standard and the most
mechanically load-bearing.

```jsonc
"format": {
  "mode": "live",                      // live | hybrid | self-paced | recorded
  "cohort_start": "2026-09-06",        // FUTURE dates only — see below
  "enrollment_status": "open",         // open | closing_soon | waitlist | closed
  "schedule": "Sat & Sun, 20:00-23:00 IST",
  "duration": "12 months",
  "duration_hours": 45.5,              // self-paced courses; null for live
  "access": "1.5 years dashboard access",
  "recording_available": true,
  "content_updated": "2026-07-01",     // last real curriculum revision
  "curriculum_version": "3.0"
},
"cohort_start_stale": "2025-01-25"     // TOP LEVEL, past cohorts only
```

### `mode` — the live-vs-recorded axis
| value | meaning |
|---|---|
| `live` | Scheduled cohort, instructor present, fixed calendar. |
| `hybrid` | Live sessions **plus** a durable recorded library you can start today. |
| `self-paced` | Pre-recorded, always-on, no cohort (Udemy, on-demand). |
| `recorded` | Archive of a *past* live cohort, sold as replay. Distinct from `self-paced`: the content was built for a cohort that has ended. |

### The two date fields, and why they are two
`cohort_start` holds **only future** cohorts. `cohort_start_stale` holds **past** ones.

A past date in `cohort_start` is the single most dangerous defect in this schema, because the
generate node reads `cohort_start` as a persuasion fact — it would tell a user in August 2026 to
enroll for a cohort that began in January 2025. When a cohort passes, move the value to
`cohort_start_stale` (reference only, excluded from generate context) and either set the next
cohort or leave `cohort_start` null. The validator warns on any past `cohort_start`.

### `content_updated`
The last time the **curriculum actually changed** — not the last time the page was touched. Only
set it if the source states or clearly implies it (a version bump, a "updated Jul 2026" badge, new
modules covering something that didn't exist a year ago). Leave it null otherwise; the scorer has a
defined behavior for unknown, and a guessed date is worse than no date.

### `curriculum_version`
Sponsors ship overlapping versions of the same course. The 3.0 landing page lists 32 modules while
the PDF attached to it is the 2.0 syllabus with 27. Record the version you curated from, and note
the skew in `_comment_modules`. Never merge two versions' module lists into one file.

---

## 4. Content structure

### `module_groups` — array of `{group, modules[]}`, target 6–12 groups
**Never emit one chunk per module.** Two independent reasons, both load-bearing:
1. Three-word module titles ("AWS Lambda", "LangSmith") embed terribly — almost no semantic signal.
2. 28 vectors from a single course dominate RRF and starve the other 59 courses out of the
   candidate set.

So: cluster flat module lists into 6–12 **thematic** groups, each becoming one chunk. The validator
warns above 12. A course with no `module_groups` is valid (objectives-only, fine for the long tail)
but should never be a hero course.

### `projects` — array of strings
Hands-on deliverables. One chunk. High persuasion value; also genuinely differentiating between
otherwise-similar courses.

### `career_roles` — array of strings
A **card field, not an embedded field**. Embedding roles makes every course match every "how do I
become an X" query, collapsing retrieval precision.

---

## 5. The ladder — `prereq_ids` / `related_ids`

Arrays of slugs. This is the entire "graph-aware retrieval" claim (arch A10/R2) — two ID lists, not
a knowledge graph. They drive the card's **Next step** line and a `+0.05` fusion adjacency boost.

- Every referenced slug must exist in the catalog at ingest time — `validate_graph()` fails loudly.
- Forward references to courses you haven't curated yet are **fine during curation**; the validator
  reports them as pending so you can confirm spelling later.
- Keep `related_ids` to 2–4. It carries similar/alternative semantics both; there are deliberately
  no `similar_ids` / `bundle_ids` / `alternative_ids` arrays (arch C4).

---

## 6. Freshness scoring — live vs recorded, and recency

Implemented in [app/catalog/freshness.py](../app/catalog/freshness.py), consumed by `fusion_rank`.
The premise: **people want live and they want recent.** A cohort starting in four weeks is a
materially better recommendation than the same syllabus recorded eighteen months ago, and no amount
of embedding similarity captures that — it's a fact about time, so it's scored deterministically.

```
freshness = enrollment_multiplier × (0.55 · mode_prior + 0.45 · recency)
```

**Mode prior** — the live-vs-recorded axis:

| mode | prior | why |
|---|---|---|
| `live` | 1.00 | Mentor access, cohort peers, and it's necessarily the newest revision. |
| `hybrid` | 0.85 | Same upside, but startable today, so less scarcity pull. |
| `self-paced` | 0.60 | Always available; value rests entirely on how current it is. |
| `recorded` | 0.45 | Replay of a finished cohort — no mentor, and the content is by definition older. |

**The demotion rule.** A `live` or `hybrid` course with **no future cohort** is scored at the
`recorded` prior. Not because the file is wrong — the course really is sold as live — but because
what a buyer gets *today* is recordings of a cohort that already ran. Without this, a dead January
2025 cohort would outrank a genuinely upcoming one purely on the strength of the word "live" in its
metadata. `effective_mode()` returns the reason alongside the mode, and `declared_mode` is preserved
in the result so the card can still say "live bootcamp" truthfully.

**Recency** — measured from whichever date is meaningful for that mode, not one global rule:

- *Live/hybrid with a future `cohort_start`:* scored on **days until start**. 0–60 days out → `1.0`
  (enrollable now, which is what a user actually wants). Beyond 60 days it decays with a 240-day
  scale — a cohort a year out is real but not urgent. If the cohort has already **started**,
  recency collapses on a 30-day half-life: the seat is gone and the value drops fast.
- *Everything else:* half-life decay on `content_updated`, else `published_at`, else
  `cohort_start_stale`. **365-day half-life** — a year-old curriculum scores `0.5`, which is the
  right verdict in a field where the frameworks turn over annually.
- *No date at all:* `0.35`. Deliberately below the one-year mark. Unknown recency must never
  out-rank known-fresh, and we do not fabricate a date to fill the gap.

**Enrollment multiplier:** `open`/`closing_soon` → `1.0`, `waitlist` → `0.7`, `closed` → `0.25`.
A penalty, not an exclusion, consistent with arch B4 — a 60-course catalog starves under hard
exclusions. `closing_soon` also raises an `urgency` flag the generate node may use in copy.

**Fusion weight.** Freshness enters the deterministic rank at `0.10`, taken proportionally from the
existing terms so the vector still sums to 1.0:

```
0.40·norm(rrf) + 0.28·interest_match + 0.10·freshness
              + 0.09·popularity + 0.08·rating_prior + 0.05·graph_adjacency
```

`score_freshness()` returns an `explain` dict alongside the number so the README can show the
breakdown and the card can justify itself, and a `label` string that is **only ever populated with
future dates** — the stale-date rule from §3 holds all the way through to rendered copy.

---

## 7. `_comment_*` fields — the audit trail

Any key starting with `_comment_` is a curation note. They are stripped before embedding and before
SQL write, and they are the reason this dataset is trustworthy. Write one whenever you make a
judgment call:

| when | example key |
|---|---|
| You paraphrased marketing prose | `_comment_overview` |
| You dropped uncorroborated objectives | `_comment_objectives` |
| Sources disagree (module counts, versions) | `_comment_modules`, `_comment_sources` |
| You chose null over a number | `_comment_price`, `_comment_rating` |
| A field is stored but not yet scored | `_comment_level` |

`null` with a comment beats a plausible guess, every time. The validator warns on `rating: null` —
that warning is *expected* and correct; it exists to make you confirm you didn't fabricate.

---

## 8. Workflow

```bash
python Experiment/collect_helper.py <url>          # dump cleaned page text
python Experiment/collect_helper.py <url> --pdf    # dump syllabus PDF
#   read the dump, hand-write the JSON — that reading IS the QA step
python data/data_1/validate_seed.py data/data_1    # structure + curation rules
python -m app.catalog.ingest                       # chunk → embed → Chroma
```

Errors block ingest. Warnings are prompts to confirm, not to silence — every warning in the
current dataset corresponds to a real judgment call recorded in a `_comment_*`.
