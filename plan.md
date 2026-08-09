# Plan: compressed background context + a per-user knowledge store

Goal: cut the token cost of `WHAT YOU KNOW ABOUT THIS USER` — the knowledge
block `build_user_context()` (app/chat/agent.py) rebuilds from scratch and
sends **verbatim, in full, on every chat turn** — by extracting it once and
reusing a compact form, instead of reconstructing and resending it N times
per conversation. This is separate from the ChromaDB RAG (course-catalog
retrieval) and from `app/chat/context.py`'s conversation-history compaction,
both of which already do the right thing and are out of scope below.

## 1. Where the tokens actually go today

Measured via the `llm_call_log` telemetry just added (app/agent/telemetry.py):
one real chat turn against Groq, with a **near-empty** profile (no resume, no
stated budget, one behavioral interest line), cost **2,150 prompt tokens**.
That number is a floor — it grows with every field a real profile/resume
fills in, and it is paid again on **every turn of the conversation**, not once
per profile.

`build_user_context()` (app/chat/agent.py:126-225) already extracts specific
fields rather than dumping the raw resume — bio, goals[:400 chars],
skills[:25], budget/hours, top-5 interests, ATS score + missing/matched
skills[:10/12], the current recommendation's narrative[:300] and top-4 items'
reasoning. That's reasonable per-field discipline. The waste is structural,
not per-field: **all of it is rebuilt from `UserProfile` + `ResumeAnalysis` +
`Recommendation` and resent as text on every single call**, whether the
profile changed since the last turn or not.

Contrast with `app/chat/context.py`, which already solves the identical
problem for conversation history: older turns are compacted into a few lines
of durable fact *once*, deterministically (no LLM call), and that compact
form is reused rather than resending the whole thread. The background block
needs the same treatment.

## 2. Proposal

### 2a. A stored "background brief", generated at write-time not read-time

Add `background_brief: Text` to `UserProfile` (and/or `ResumeAnalysis` — see
open question below). Generate it **once**, deterministically (Python string
templating, not an LLM call — same reasoning `context.py`'s docstring already
gives: "a summary call on every turn doubles the LLM cost of chatting, and a
model summarizing 'my budget is 5000' as 'the user mentioned budget'
destroys the number that made it useful"), whenever the source data actually
changes:

- On profile save (`app/profiles/routes.py`)
- On resume analysis (`app/profiles/ats.py:analyze()` completing)
- On a new recommendation being generated (`app/agent/graph.py`)

`build_user_context()` then reads the **stored** brief instead of
re-deriving+re-formatting from three tables on every turn. Regeneration is
proportional to how often a profile changes (rare); reads are proportional to
how often someone chats (frequent) — that asymmetry is the entire saving.

### 2b. A per-user knowledge store, separate from Chroma

Chroma stays exactly what it is — course-catalog vectors for retrieval — and
this plan does not touch it. What's missing is the equivalent structured
store for **the user's own background**, so the chat prompt and the
recommendation pipeline both read one small, typed record instead of each
independently re-querying and re-formatting `UserProfile` +
`ResumeAnalysis.is_current` + `Recommendation.is_current`.

Concretely: promote the ad hoc extraction already happening (in
`build_user_context()` and `context.py:extract_facts()`) into one first-class
table, e.g.:

```
UserKnowledge
  user_id (FK, unique)
  target_role, budget_max, weekly_hours       # declared, filter-relevant
  resume_gaps (JSON, top-N)                    # from ResumeAnalysis
  top_interests (JSON, top-5)                  # from UserProfile.interests
  background_brief (Text)                      # the rendered compact prompt block
  source_fingerprint (String)                  # hash of the rows it was built from
  updated_at
```

`source_fingerprint` is the cache-invalidation key — same pattern the
recommendation pipeline already uses (`fingerprint()` in
app/catalog/... / `Recommendation.fingerprint`) for "has the input actually
changed". `build_user_context()` becomes one indexed read instead of three
queries plus string assembly, every turn.

### 2c. Interaction with the usage guardrails just built

`app/chat/guardrails.py` now caps tokens per session/user/day
(`CHAT_SESSION_TOKEN_LIMIT` etc., app/config.py). Shrinking the fixed
per-turn overhead directly multiplies how many turns fit under those caps —
this plan and the guardrails compound rather than overlap.

## 3. Phased implementation

1. **Deterministic brief renderer.** A pure function,
   `render_background_brief(profile, ats, rec) -> str`, extracted from the
   existing body of `build_user_context()` with no behavior change — this is
   a refactor step, not a rewrite, so it's low-risk and independently
   testable.
2. **Store + invalidate.** Add `background_brief` (+ `source_fingerprint`) to
   `UserProfile`. Call the renderer and write the result at the three
   write-time trigger points above, gated on the fingerprint actually
   changing.
3. **Read instead of rebuild.** `build_user_context()` reads the stored
   brief when the fingerprint matches; falls back to rendering (and storing)
   on a miss — so a never-populated row (existing users, first deploy)
   degrades to today's behavior rather than erroring.
4. **Measure.** Compare `llm_call_log.prompt_tokens` before/after on the same
   query, same user — the telemetry to do this already exists as of this
   session.
5. **(Optional, only if 1-4 aren't enough)** Split into the full
   `UserKnowledge` table from §2b if the single-string brief proves too
   coarse for code that wants structured fields (e.g. `budget_max` as a
   float for retrieval filtering) rather than prompt text — right now
   `facts["budget_max"]` already comes from conversation-turn extraction
   (`context.py`), so this is genuinely optional, not a prerequisite.

## 4. Open questions

- **Brief lives on `UserProfile` or a new table?** Leaning `UserProfile`
  (one more column) over a new table for phases 1-4 — it's a 1:1 relationship
  already, and a new table earns its keep only if §2b's structured fields
  turn out to be needed by more than one caller. Revisit after step 4's
  measurement.
- **Does the brief regenerate on *every* profile field change, or only
  resume/target-role/skills changes?** Regenerating on every keystroke-level
  save (e.g. `preferred_mode` toggled) is wasted work if it doesn't move the
  brief's content — the fingerprint should probably hash only the fields
  that actually appear in the rendered brief, not the whole row.

## 5. Explicitly out of scope

- ChromaDB / course-catalog retrieval — unaffected, this plan is about the
  user's own background, not course search.
- Conversation-history compaction (`app/chat/context.py`) — already solves
  this exact class of problem correctly; nothing to change.
- Resume text sent to an LLM — it isn't, today (`app/profiles/ats.py:analyze()`
  is deterministic Python, confirmed while researching this plan). No change
  needed there.

---

## 6. Full inventory: what's in the DB vs what reaches an LLM

Audited every call site. There are exactly **three** places a prompt is
built — everything else that touches `UserProfile`, `ResumeAnalysis`,
`Product`, or conversation history is either stored-only or deterministic
Python.

| Table / field | Ever sent to an LLM? | Where |
| --- | --- | --- |
| `UserProfile.resume_text` (full text) | **No** | `app/profiles/ats.py:analyze()` scores it with regex/keyword matching, not a model call |
| `UserProfile` declared fields (bio, goals, skills, budget, hours…) | Yes, per-turn | chat knowledge block (`build_user_context()`) |
| `ResumeAnalysis.matched_skills` / `missing_skills` | Yes, per-turn (chat) and per-run (recommend) | chat knowledge block; `generate.py` profile_lines |
| `Recommendation.narrative` / `.items[].hook,reason` | Yes, per-turn | chat knowledge block (as "ALREADY RECOMMENDED") |
| `Product.description` | Yes, truncated | every retrieval-adjacent call, at **three different truncation lengths** (below) |
| `ChatMessage` history | Yes, split by horizon | recent N verbatim, older compacted (`context.py`) — already optimal |
| `Event` (browsing behavior) | **No** | only ever reaches `scorer.py` (deterministic dual-horizon decay), never a prompt |
| Chroma vectors | N/A | embeddings, not text — never "sent" as prompt content, only used for similarity search |

**The three LLM call sites**, what each actually receives, and current volume:

1. **Chat agent** (`app/chat/agent.py` → `langchain_bridge.py`, every chat turn).
   Fixed `SYSTEM_PROMPT` (~700 words ≈ 950-1100 tokens, **identical on every
   single call** — including every retry and every tool-loop iteration within
   one turn) + `build_user_context()` knowledge block + `context.py` compacted
   facts + up to 8 recent messages verbatim + first-pass catalog block (up to
   `RETRIEVE_K=6` courses, `_fmt_course()`, description **[:280 chars]**).
   Measured: 2,150 prompt tokens on a thin profile with zero history.

2. **`generate.py:run()`** (recommendation narrative + card copy, one call per
   recommendation refresh). `SYSTEM` (~150 words) + up to `TOP_K=5` ranked
   courses via `_fmt()` (description **[:200 chars]**) + `profile_lines`
   (target_role, experience, gaps[:8], interests top-4, goal[:200], budget).

3. **`rerank.py:_llm_rerank()`** (`RERANK_MODE=llm` only — **off by default**,
   `cross_encoder` is the configured mode, so this costs **zero tokens today**
   unless someone flips the setting). Would send up to `RERANK_POOL=16`
   candidates, description **[:160 chars]**.

Three different truncation lengths for the same field (`Product.description`
at 160/200/280 chars depending on call site) is not a bug, but it is
unprincipled — each was probably tuned locally rather than against a shared
budget. §8 below turns this into one lever.

## 7. Per-call precision: RAG + knowledge DB + reranker, shed and compress

The instruction to make every call "precise" — RAG + knowledge DB + reranker
supplying exactly what's needed, nothing extra — is mostly already true
structurally and only partly true in volume:

**Already precise (structural):**

- Retrieval → rerank → generate is a **funnel**, not a dump: `CANDIDATES=24`
  per retriever → RRF fusion → `RERANK_POOL=16` → reranked → `top_k=6`
  actually reach a prompt. The catalog is never sent whole.
- Grounding is enforced twice (chat: `_enforce_grounding`; recommend: the
  `allowed` id-set check in `generate.py`) — so shedding aggressively on the
  way in is safe, because a course the model can't see, it can't cite.
- `max_price` is a SQL filter, not a prompt instruction (§ note in
  `retrieve()`) — that's the correct place for a hard constraint; asking the
  model to "please respect the budget" would spend tokens on something SQL
  already guarantees.

**Not yet precise (volume / redundancy):**

- **The fixed `SYSTEM_PROMPT` is the single largest, most repeated cost**, and
  it is identical on every call — this is squarely a caching problem, not a
  content problem (see §8.1).
- **The knowledge block is rebuilt in full every turn** regardless of whether
  it changed (§1-§3 above) — this is the "knowledge DB" gap: there is no
  cached, precise, per-user record to hand the prompt builder; it re-derives
  from three tables every time.
- **Inconsistent description truncation** (160/200/280 chars, §6) means the
  same course's description is a different length depending on which call
  path touched it last — no shared "how much course detail does a prompt
  need" budget exists.
- **The chat knowledge block sends candidate courses as free text**
  (`_fmt_course`, pipe-joined key:value bits) rather than the terser form
  `generate.py:_fmt()` already uses for the same job (id, title, category,
  level, price, a short "why" line) — two formatters solving the same
  problem at different verbosity.

## 8. Token-reduction levers, prioritized

Ordered by (impact × how well-understood the fix is), not effort:

1. **Prompt caching on the fixed `SYSTEM_PROMPT` prefix.** It is ~1,000
   tokens, byte-identical on every chat call — every retry, every tool-loop
   iteration, every turn. OpenAI-compatible APIs (which Mesh, Groq, and Ollama
   all present here) increasingly cache a stable prefix automatically past a
   length threshold, billing repeat hits at a fraction of input cost — but
   this needs verifying against Mesh's and Groq's actual `/models` /billing
   docs, not assumed. If unsupported, the same effect is achievable by
   keeping the system prompt maximally stable (never string-formatting
   per-turn content into it — already true here, since knowledge lives in a
   separate message) and short. **Unverified — first step is checking
   whether Mesh/Groq support and are actually applying prefix caching.**
2. **§1-5 above: cache the knowledge block instead of rebuilding it.**
   Measured 2,150 prompt tokens per turn on a thin profile; grows with every
   profile/resume field filled in, paid every turn. Highest-confidence lever
   here because it's fully within this codebase's control (no provider
   feature dependency).
3. **Unify description truncation to one shared, deliberately short budget**
   (§6/§7) — e.g. 150 chars everywhere a course description enters a prompt,
   picked once and reused, rather than three ad hoc numbers. Small per-call
   saving, but multiplies by every course in every prompt.
4. **Reduce `RETRIEVE_K` / `RERANK_POOL` sizes only if measurement shows
   headroom** — not proposed blindly: `RERANK_POOL=16` exists specifically so
   the reranker has more to choose from than it returns (`retrieve()`'s own
   comment). Shrinking it risks answer quality for a token saving that's
   already small next to levers 1-2. Flagged as **measure before touching**,
   not a recommended cut.
5. **Retry token cost — see §9.** A retried call re-sends the *entire* prompt
   again; every retry avoided is a full duplicate payment, not a partial one.
6. **The chat agent's own tool calls** (`search_catalog`, `get_course_details`)
   inject their results back into the message history for the rest of that
   turn's tool loop — each subsequent model call in the same turn re-sends
   everything the previous tool call returned, compounding within a single
   turn the same way the knowledge block compounds across turns. Not
   addressed here; worth its own pass once §2/§8.2 lands, using the same
   `llm_call_log` telemetry to measure whether it's actually significant
   before optimizing it.

## 9. Retry / backoff optimization

Current logic (`app/agent/mesh.py:_chat()` and
`langchain_bridge.py:mesh_fallback_middleware`, duplicated in both): up to 3
attempts per provider on `RETRY_STATUS = {429, 500, 502, 503}`, fixed
exponential backoff `delay=1.0` then `*=2` (sleeps 1s, then 2s — no sleep
after the 3rd attempt, it just gives up and moves to the next provider). No
jitter, no `Retry-After` header read, same fixed sequence regardless of which
status code triggered it.

**Why this matters for tokens, not just latency:** a retry re-sends the
*entire* prompt — there is no partial-credit retry. Three attempts against a
provider that's genuinely rate-limited means paying for the same prompt
tokens up to three times before the circuit breaker or the provider chain
gets a chance to move on. Fewer, smarter retries is a token lever, not only a
latency one.

Concrete changes, in order of confidence:

1. **Honor `Retry-After` when the provider sends one**, instead of guessing
   with a fixed 1s/2s sequence. 429 responses commonly include it; an
   `APIStatusError`'s `.response.headers` carries it on the OpenAI SDK path
   used here. Falls back to the current fixed backoff when absent — strictly
   additive, no regression risk.
2. **Add jitter** (e.g. `delay * random.uniform(0.5, 1.5)`) to the existing
   exponential sequence. Currently every concurrent request retrying the same
   overloaded provider backs off in lockstep — a thundering-herd pattern that
   makes the rate limit worse right when it's already been hit. Standard
   practice, no downside.
3. **Drop to 2 attempts per provider, not 3.** With the token-repayment cost
   in mind: the third attempt's marginal chance of success (after two 429s in
   ~3s) is low, and the chain already has a next provider to fall to. Cuts
   worst-case per-provider backoff from 3s to 1s and worst-case token
   repayment from 3x to 2x. **Needs `llm_call_log`'s `attempt` column
   (already recording this) to confirm empirically how often attempt 3
   actually succeeds before cutting it** — don't remove blind.
4. **Cap total retry wall-clock per provider** (e.g. abandon early if backoff
   would exceed ~5s) rather than trusting attempt-count alone to bound it —
   belt-and-suspenders once (1) and (2) make the delay less predictable.
5. Both call sites (`mesh.py`, `langchain_bridge.py`) duplicate this exact
   retry loop. Once tuned, worth extracting to one shared helper so the two
   never drift — not urgent, but flagged since duplication is exactly how the
   old fixed-25 `recursion_limit` bug and the fixed-3-truncation-lengths (§6)
   both happened: the same logic tuned twice, differently, by accident.

---

## 10. Proactively ask how much time they want to invest

Today, `weekly_hours` is only used when it's already known — either stated on
the profile or volunteered mid-conversation (`extract_facts` in
`context.py`). Nothing prompts for it. The closest existing behavior is the
generic fallback line in `build_user_context()`: *"Nothing known about this
user yet... Ask one short question to orient"* — that fires only when the
profile is completely empty, and it doesn't specifically ask about time.

A course and a 15-minute primer are different answers to the same question
depending on whether someone has 2 hours or 10 hours a week, and whether they
want an outcome in two weeks or two months — so this is worth asking early,
not inferring.

**Proposal:** on the *first* turn of a conversation (`history` empty) where
`weekly_hours` is still unknown and the message is a genuine learning/role
question (not small talk), the reply should ask it directly alongside
whatever else it says — not as a separate empty turn, since that wastes a
round trip against the same guardrails this session just built. Two ways to
drive that behavior, cheapest first:

1. **Prompt-level nudge.** Add a `TIME COMMITMENT: unknown — ask directly in
   this reply` line to the knowledge block (`build_user_context()` /
   `answer()`) exactly when `facts.get("weekly_hours")` is empty and
   `history` is empty. Zero new tools, zero new DB columns — reuses the
   pattern that already governs "no resume uploaded yet" nudges. `weekly_hours`
   already round-trips through `update_learner_profile` (the HITL-confirmed
   tool in `app/chat/tools.py`) the same way budget does today, so nothing
   new is needed to *save* the answer once given.
2. **A durable "timeline" fact**, not just weekly hours — e.g. "I want to be
   job-ready in 2 months" is a deadline, not an hours/week number, and the
   two aren't interchangeable (10h/week for 2 months ≠ 10h/week with no
   deadline). If this turns out to matter, it's a second nullable column
   (`UserProfile.target_timeline` or similar) fed by the same
   `update_learner_profile` tool with one more optional argument — deferred
   until (1) shows whether "weekly hours" alone is answer enough.

**Implemented**: the prompt-level nudge (1) — `build_user_context()` now
reports `weekly_hours` in its `facts` dict, and `answer()` appends a
`TIME COMMITMENT: not stated yet` line to the knowledge block on the first
turn of a conversation when neither the stored profile nor anything said so
far in this thread carries an hours figure. No schema change. (2), the
separate timeline/deadline field, stays deferred until (1) proves
insufficient in practice.
