# Graph Report - smartreco  (2026-08-11)

## Corpus Check
- 124 files · ~192,371 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3855 nodes · 9702 edges · 216 communities (178 shown, 38 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 896 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `82db8183`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- score_freshness
- Personalized Recommendations Page
- admin/routes.py
- validate node — THE GROUNDING GUARANTEE
- catalogue.py
- course.schema.json
- test_smoke.py
- User
- session.py
- SmartReco Course JSON Standard v1.0
- properties
- test_mail.py
- Deterministic Trigger Policy — the planner
- properties
- enum
- type
- null
- module_groups
- level_range
- embed_batch
- mermaid.min.js
- r
- mesh.py
- node
- enum
- SmartReco Checks Workflow
- models.py
- deterministic_grade node
- enum
- enum
- enum
- Signed-Cookie Email/Password Authentication
- enum
- push
- currency
- objectives
- insert
- source_urls
- related_ids
- test_outbox.py
- Experiment/chunker.py
- agent.py
- forEach
- X8
- analyze
- consumeInternal
- SmartReco — Foundation Design
- isInstance
- pU
- peekChar
- test_profiles.py
- handler
- data_1/load_courses.py
- Experiment/load_courses.py
- get
- test_chat.py
- Product
- generate.py
- chat/routes.py
- test_rerank.py
- test_providers.py
- DS
- AWS SageMaker
- personas.py
- datetime
- setup.sh
- tracker.js
- smartreco
- conftest.py
- Qt
- build_pathway_async
- auth/routes.py
- r8
- mFe
- update
- .toString
- indexOf
- mesh_fallback_middleware
- rerank.py
- setAttribute
- build
- chat.js
- lineTo
- constructor
- qze
- visit
- extract_facts
- some
- htmlBuilder
- z_e
- _p
- test_tracker.js
- providers.py
- splice
- tn
- concat
- Gi
- covers.py
- expandOnce
- score_intent
- setData
- parseInline
- performStartup
- Plan: compressed background context + a per-user knowledge store
- _P
- _date
- sync_sql.py
- checkIsTarget
- atLeastOneInternal
- getKeyForAutomaticLookahead
- defineRule
- use
- freshness.py
- performSelfAnalysis
- clear
- subruleInternal
- manyInternal
- optionInternal
- orInternal
- Deterministic Fusion Ranking (RERANK_MODE=fusion)
- data_1/validate_seed.py
- atLeastOneSepFirstInternal
- getLaFuncFromCache
- enableRecording
- lex
- jo
- manySepFirstInternal
- C1e
- Settings
- subrule
- Ym
- tokenizeInternal
- link
- ei
- g
- flat
- e0
- Freshness Scoring Specification (schema §6)
- accept
- acquireParserWorker
- aO
- _comment_* fields — the curation audit trail
- tryInRepetitionRecovery
- reset_breakers
- _7
- kFe
- lAe
- _cache_key
- l8e
- getAllSubTypes
- mO
- Ul
- table
- D8
- jge
- BF
- cve
- q6
- rYe
- getAllTags
- HCe
- zB
- hy
- Y6
- O7
- qR
- Pn
- rV
- A7
- BG
- bv
- write
- _ce
- Ll
- D7
- Dpe
- g7e
- findDeclaration
- gat
- getAstNodePath
- getBaseCstVisitorConstructor
- parseClassifier
- hTe
- Ij
- Jf
- lze
- rBe
- l5e
- SS
- MPe
- vTe
- Npe
- o3
- pOe
- w1e
- resetStackSize
- terminateWorker
- W_e
- wj
- Zge

## God Nodes (most connected - your core abstractions)
1. `push()` - 241 edges
2. `r()` - 236 edges
3. `n()` - 194 edges
4. `a()` - 154 edges
5. `l()` - 101 edges
6. `t()` - 78 edges
7. `h()` - 77 edges
8. `Product` - 74 edges
9. `node()` - 67 edges
10. `forEach()` - 66 edges

## Surprising Connections (you probably didn't know these)
- `Agent Run Telemetry Record` --semantically_similar_to--> `LLM Observability`  [INFERRED] [semantically similar]
  app/web/templates/admin/agent_runs.html → data/data_1/Agentic And GenAI AWS GCP.pdf
- `Pending Vector Sync Outbox` --semantically_similar_to--> `Vector Databases and Embeddings`  [INFERRED] [semantically similar]
  app/web/templates/admin/dashboard.html → data/data_1/Agentic And GenAI AWS GCP.pdf
- `Course Ladder (prereq / related slugs)` --semantically_similar_to--> `Course Prerequisites (Python, NLP, GenAI)`  [INFERRED] [semantically similar]
  app/web/templates/admin/product_form.html → data/data_1/Agentic And GenAI AWS GCP.pdf
- `rec_dismiss Negative Interest Signal (5.2)` --semantically_similar_to--> `Human-in-the-Loop Workflows`  [INFERRED] [semantically similar]
  app/web/templates/web/recommendations.html → data/data_1/Agentic And GenAI AWS GCP.pdf
- `Concurrency cancel-in-progress group` --semantically_similar_to--> `Deterministic Trigger Policy — the planner`  [INFERRED] [semantically similar]
  .github/workflows/smartreco-build-challenge-2026-checks.yml → SmartReco_Architecture_v2_FINAL.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **LangGraph two-LLM happy path flow** — smartreco_architecture_v2_final_analyze_behavior_node, smartreco_architecture_v2_final_retrieve_node, smartreco_architecture_v2_final_fusion_rank_node, smartreco_architecture_v2_final_deterministic_grade_node, smartreco_architecture_v2_final_generate_node, smartreco_architecture_v2_final_validate_node, smartreco_architecture_v2_final_refine_query_node [EXTRACTED 1.00]
- **Authentication, role gate, and identity-stitching flow** — app_web_templates_auth_register_register_template, app_web_templates_auth_login_login_template, app_web_templates_base_nav_role_gate, smartreco_architecture_v2_final_cookie_session_auth, smartreco_architecture_v2_final_require_admin, smartreco_architecture_v2_final_identity_stitching [INFERRED 0.85]
- **The efficiency argument — deterministic gating before any LLM call** — smartreco_architecture_v2_final_efficiency_thesis, smartreco_architecture_v2_final_dual_horizon_scorer, smartreco_architecture_v2_final_fingerprint, smartreco_architecture_v2_final_trigger_policy, smartreco_architecture_v2_final_deterministic_first_grading, smartreco_architecture_v2_final_embedding_cache, smartreco_architecture_v2_final_agent_runs [INFERRED 0.85]
- **Templates reusing the shared course card partial** — app_web_templates_catalog__card_course_card, app_web_templates_catalog_detail_course_detail, app_web_templates_catalog_index_catalog_index, app_web_templates_catalog_search_catalog_search [EXTRACTED 1.00]
- **Behavior tracking to recommendation telemetry flow** — app_web_templates_catalog__card_data_product_id_contract, app_web_templates_catalog_detail_dwell_tracking, app_web_templates_web_recommendations_rec_dismiss_signal, app_web_templates_web_recommendations_recommendations_page, app_web_templates_admin_agent_runs_agent_run_telemetry [INFERRED 0.85]
- **LangChain ecosystem observability tooling** — data_data_1_agentic_and_genai_aws_gcp_langfuse, data_data_1_agentic_and_genai_aws_gcp_langwatch, data_data_1_agentic_and_genai_aws_gcp_langsmith, data_data_1_agentic_and_genai_aws_gcp_llm_observability [EXTRACTED 1.00]

## Communities (216 total, 38 thin omitted)

### Community 0 - "score_freshness"
Cohesion: 0.17
Nodes (27): generate_context(), Tier 3 — compressed injection, ~130 tokens/candidate (arch D2). Ships the top-3…, effective_mode(), The 0.10 fusion term, plus everything needed to justify it. Returns {score,…, Returns (mode_for_scoring, reason). The one non-obvious rule in this module: a…, score_freshness(), course(), Tests for the live-vs-recorded + recency scoring (data/COURSE_SCHEMA.md §6).… (+19 more)

### Community 1 - "Personalized Recommendations Page"
Cohesion: 0.05
Nodes (53): Agent Run Telemetry Record, Admin Agent Runs Table, LLM Call Efficiency Metric (arch 1.2), Admin Dashboard, Catalog Ingest Action, Pending Vector Sync Outbox, Course Ladder (prereq / related slugs), Admin Product Form (+45 more)

### Community 2 - "admin/routes.py"
Cohesion: 0.06
Nodes (72): agent_runs(), create_product(), dashboard(), delete_product(), edit_product_form(), list_products(), llm_usage(), mail_dashboard() (+64 more)

### Community 3 - "validate node — THE GROUNDING GUARANTEE"
Cohesion: 0.14
Nodes (18): career_roles — a card field, not an embedded field, Experiment/collect_helper.py — page and PDF text dumper, The Corroboration Rule (schema statement), Curation Workflow — collect_helper, validate, ingest, module_groups — thematic clustering, never one chunk per module, NEVER_EMBED_KEYS enforcement, objectives — highest-value retrieval keys, The Three Tiers — Filter / Embed / Persuade (+10 more)

### Community 4 - "catalogue.py"
Cohesion: 0.18
Nodes (18): _add_chunk_share(), build_catalogue(), _comment_count(), _counts(), _course_flags(), _flag(), _is_placeholder_url(), main() (+10 more)

### Community 5 - "course.schema.json"
Cohesion: 0.09
Nodes (21): additionalProperties, allOf, description, type, description, $id, patternProperties, ^_comment (+13 more)

### Community 6 - "test_smoke.py"
Cohesion: 0.50
Nodes (4): asyncio, Smoke tests — /healthz and non-blocking event ingest. Run: make test. ENV=test…, test_event_ingest_202(), test_healthz()

### Community 7 - "User"
Cohesion: 0.05
Nodes (70): maybe_generate(), Consult the planner, then run only if it says so (§5.2). This is the entry…, check_models_at_startup(), Verify every configured model is actually offered by Mesh. Don't assume. Uses…, current_user(), identity_middleware(), Request, Request-scoped identity: middleware + the two FastAPI dependencies.… (+62 more)

### Community 8 - "session.py"
Cohesion: 0.06
Nodes (36): _embed_local(), _load_local(), Embeddings, with a local fallback that cannot run out of balance. Groq — the…, Load the sentence-transformers model once, off the event loop.…, Encode with nomic-embed. nomic is an ASYMMETRIC model: it expects…, Per-call LLM telemetry: one row in `llm_call_log` per provider attempt. Written…, record_llm_call(), BudgetCheck (+28 more)

### Community 9 - "SmartReco Course JSON Standard v1.0"
Cohesion: 0.12
Nodes (18): tracker.js deferred script include, SmartReco Course JSON Standard v1.0, course.schema.json — structural schema, How Recommendations Are Grounded (README §4), SmartReco README Outline, APScheduler Job Schedule, Deliberately Not Built — rejected ideas with stated reasons, Digest Email — bonus tier, first to be cut (+10 more)

### Community 10 - "properties"
Cohesion: 0.08
Nodes (26): description, type, description, format, pattern, description, description, type (+18 more)

### Community 11 - "test_mail.py"
Cohesion: 0.05
Nodes (88): DigestLog, cmd_check(), cmd_run_digest(), cmd_send(), main(), Mail from the command line — `python -m app.mail.cli <command>`. Exists because…, Print without assuming the console can encode it. Same guard as…, Prove or disprove that mail can actually be delivered. (+80 more)

### Community 12 - "Deterministic Trigger Policy — the planner"
Cohesion: 0.22
Nodes (13): Browsing-carries-over copy on the signup page, How We Avoid Wasteful LLM Calls (README §3), Cold-Start Floor — trending within observed signal, The Planner Is Deliberately Not an LLM, Dual-Horizon Deterministic Interest Scorer, Efficiency Thesis — LLM runs only when behavior materially changed, events table — behavioral log, Exponential-Decay Feedback Loop (rec_click / rec_dismiss) (+5 more)

### Community 13 - "properties"
Cohesion: 0.07
Nodes (27): default, type, description, type, description, minLength, type, description (+19 more)

### Community 14 - "enum"
Cohesion: 0.15
Nodes (13): description, enum, type, category, Agentic AI, Analytics, Cloud, Computer Vision (+5 more)

### Community 15 - "type"
Cohesion: 0.08
Nodes (25): description, items, type, type, items, type, description, items (+17 more)

### Community 16 - "null"
Cohesion: 0.08
Nodes (29): description, pattern, type, type, description, pattern, type, description (+21 more)

### Community 17 - "module_groups"
Cohesion: 0.12
Nodes (16): minLength, type, properties, required, description, items, maxItems, type (+8 more)

### Community 18 - "level_range"
Cohesion: 0.18
Nodes (11): $ref, description, items, maxItems, minItems, type, level_range, secondary_categories (+3 more)

### Community 19 - "embed_batch"
Cohesion: 0.36
Nodes (7): embed_batch(), ▲A5: one call for the whole batch, with read-through cache. Delegates to…, embedding_text(), main(), python -m app.db.seed — load seed/products.json into SQL + Chroma. Uses…, ▲B6: every prereq/related slug must exist. Fail loudly, before any writes., validate_graph()

### Community 20 - "mermaid.min.js"
Cohesion: 0.00
Nodes (115): _6e(), addAll(), addTokenUsingPush(), aMe(), aU(), aue(), BIe(), bU() (+107 more)

### Community 21 - "r"
Cohesion: 0.04
Nodes (108): _4(), ACTION(), alternatives(), Ar(), bB(), bCe(), bMe(), buildMismatchTokenMessage() (+100 more)

### Community 22 - "mesh.py"
Cohesion: 0.16
Nodes (16): embed_batch(), _embed_mesh(), Embed a batch, cached, with automatic failover to the local model. `is_query`…, _chat(), _parse_json(), Single gateway for all Mesh API calls: chat, structured chat, embeddings. ▲A2…, ▲B1 last line of defense: some models return prose-wrapped or fence-wrapped…, One chat call, Mesh first and Groq if Mesh cannot serve it. `model` is kept for… (+8 more)

### Community 23 - "node"
Cohesion: 0.06
Nodes (108): aD(), Ane(), aOe(), bOe(), br(), children(), cIe(), cne() (+100 more)

### Community 24 - "enum"
Cohesion: 0.22
Nodes (9): source_platform, default, enum, type, coursera, other, own_site, udemy (+1 more)

### Community 25 - "SmartReco Checks Workflow"
Cohesion: 0.15
Nodes (13): Platform CI Workflow Setup Instructions, Throwaway Spend-Capped MESH_API_KEY Policy, Concurrency cancel-in-progress group, Download checks step, OIDC_AUDIENCE opaque identifier, Request GitHub OIDC token step, Run checks step, SmartReco Checks Workflow (+5 more)

### Community 26 - "models.py"
Cohesion: 0.07
Nodes (46): generate_recommendations(), The recommendation pipeline — retrieve → rank → generate → validate → store.…, Demote the old current set and insert the new one, in one transaction. Old sets…, One row per run in `agent_runs` — what /admin/agent-runs reads. Written for…, Run the pipeline for one user and store the result. Returns a report. Never…, _record_run(), _store(), load_events() (+38 more)

### Community 27 - "deterministic_grade node"
Cohesion: 0.14
Nodes (16): Header search form (GET /search), agent_runs — observability table, analyze_behavior node (LLM #1), Deterministic-First Grading, deterministic_grade node, Evaluation Harness — seven personas and ablations, Fence-Stripping JSON Parser, fusion_rank node (+8 more)

### Community 28 - "enum"
Cohesion: 0.25
Nodes (8): default, enum, type, enrollment_status, closed, closing_soon, open, waitlist

### Community 29 - "enum"
Cohesion: 0.25
Nodes (8): description, enum, type, mode, hybrid, live, recorded, self-paced

### Community 30 - "enum"
Cohesion: 0.25
Nodes (8): description, enum, type, price_band, free, high, low, mid

### Community 31 - "Signed-Cookie Email/Password Authentication"
Cohesion: 0.26
Nodes (12): login.html — login form template, register.html — registration form template, base.html — site layout shell, Nav role gate — admin link shown when role == 'admin', bcrypt Direct — passlib 1.7.4 unmaintained, Runtime Dependency Set, Build Order and Cut Order, Signed-Cookie Email/Password Authentication (+4 more)

### Community 32 - "enum"
Cohesion: 0.29
Nodes (7): description, enum, type, level, advanced, beginner, intermediate

### Community 33 - "push"
Cohesion: 0.05
Nodes (82): a(), Aet(), aie(), arc(), AS(), aze(), blockquote(), buildAlternationAmbiguityError() (+74 more)

### Community 34 - "currency"
Cohesion: 0.33
Nodes (6): default, enum, type, currency, INR, USD

### Community 35 - "objectives"
Cohesion: 0.33
Nodes (6): minLength, description, items, minItems, type, objectives

### Community 36 - "insert"
Cohesion: 0.09
Nodes (71): _5(), am(), b(), bK(), bQ(), C9(), cAe(), Cd() (+63 more)

### Community 37 - "source_urls"
Cohesion: 0.40
Nodes (5): format, source_urls, description, items, type

### Community 38 - "related_ids"
Cohesion: 0.40
Nodes (5): related_ids, description, items, maxItems, type

### Community 39 - "test_outbox.py"
Cohesion: 0.06
Nodes (59): drain_all(), drain_once(), _explain(), _is_fatal(), _mark(), Exception, Drain vector_outbox → Chroma. The half of the dual-write that does the work.…, Drain until empty or max_batches. The bound matters: without it, a row that… (+51 more)

### Community 40 - "Experiment/chunker.py"
Cohesion: 0.40
Nodes (3): generate_context(), app/catalog/chunker.py — turns a seed product into Chroma chunks (arch D2, Tier…, Tier 3 — compressed injection, ~120 tokens/candidate (arch D2). Ships the top-3…

### Community 41 - "agent.py"
Cohesion: 0.06
Nodes (57): answer(), _build_agent(), build_user_context(), _finish_turn(), _fmt_course(), _get_agent(), _history(), _level_hint() (+49 more)

### Community 42 - "forEach"
Cohesion: 0.06
Nodes (53): add(), addAstNodeRegionWithAssignmentsTo(), B7(), bAe(), CS(), Dit(), Dxe(), Ent() (+45 more)

### Community 43 - "X8"
Cohesion: 0.06
Nodes (52): aEe(), Ake(), AW(), bke(), bW(), By(), cke(), count() (+44 more)

### Community 44 - "analyze"
Cohesion: 0.07
Nodes (45): analyze(), band(), _experience_score(), _first_match(), _keyword_score(), _mentions(), parse_resume(), ATS analysis: parse a resume, score it against a target role, name the gaps.… (+37 more)

### Community 45 - "consumeInternal"
Cohesion: 0.05
Nodes (46): addToResyncTokens(), ase(), buildFullFollowKeyStack(), canPerformInRuleRecovery(), canRecoverWithSingleTokenDeletion(), canRecoverWithSingleTokenInsertion(), canTokenTypeBeDeletedInRecovery(), canTokenTypeBeInsertedInRecovery() (+38 more)

### Community 46 - "SmartReco — Foundation Design"
Cohesion: 0.04
Nodes (44): 10. Verification, 11. Deliberately not built, 1. What the foundation had to satisfy, 2. Why server-rendered, 3.1 Password hashing — bcrypt directly, not passlib, 3.2 The session cookie, 3.3 Role is read from the database, not the cookie, 3.4 Not leaking which half was wrong (+36 more)

### Community 47 - "isInstance"
Cohesion: 0.07
Nodes (44): aBe(), AN(), bT(), cbe(), dBe(), dN(), eCe(), Et() (+36 more)

### Community 48 - "pU"
Cohesion: 0.05
Nodes (42): aFe(), bA(), c8(), cFe(), cwe(), displayable(), FA(), FU() (+34 more)

### Community 49 - "peekChar"
Cohesion: 0.12
Nodes (42): alternative(), assertion(), atom(), atomEscape(), characterClass(), characterClassEscape(), classAtom(), classEscape() (+34 more)

### Community 50 - "test_profiles.py"
Cohesion: 0.09
Nodes (38): _clean(), _extract_docx(), _extract_pdf(), extract_text(), Resume intake: accept a file, keep the artifact, extract usable text. PDF and…, Read word/document.xml out of the .docx zip and strip the markup. No python-…, Collapse the whitespace noise that PDF and copy-paste extraction leave., Returns (text, warning). A non-empty warning means text is unusable. Never… (+30 more)

### Community 51 - "handler"
Cohesion: 0.12
Nodes (40): B4e(), beginGroup(), callFunction(), consume(), consumeSpaces(), endGroup(), endGroups(), expect() (+32 more)

### Community 55 - "get"
Cohesion: 0.09
Nodes (34): aRe(), b5e(), B6e(), cacheForContext(), cRe(), D_(), dehydrate(), dehydrateAstNode() (+26 more)

### Community 56 - "test_chat.py"
Cohesion: 0.11
Nodes (32): _enforce_grounding(), _offline_reply(), Strip citations — and invented course titles — that retrieval did not back.…, Deterministic answer for when Mesh is unavailable. The test suite runs offline…, fts_query(), Turn free-text into a safe FTS5 OR-query. Quoting each token individually makes…, asyncio, parametrize (+24 more)

### Community 57 - "Product"
Cohesion: 0.04
Nodes (78): _expected_level(), gap_match(), graph_adjacency(), interest_match(), level_fit(), rating_prior(), fusion_rank node — deterministic weighted ranking (arch §3, ▲A14+B2). Every…, Rating on 0-1, with an unrated course treated as average rather than as bad.… (+70 more)

### Community 58 - "generate.py"
Cohesion: 0.12
Nodes (22): Persuasive generation under a strict schema (arch v1 §5.4 generate)., writer_call(), confidence(), _fmt(), generate node — the narrative and the per-card copy. The ONE LLM call. What the…, Deterministic copy from the ranking terms. No model involved. Every sentence…, Produce narrative + per-item copy. Returns the stored `items` shape. Never…, Computed, never asked of the model (§6). Weighted toward the terms that are… (+14 more)

### Community 59 - "chat/routes.py"
Cohesion: 0.12
Nodes (30): get_or_create_conversation(), Resolve a conversation, verifying ownership. The id arrives from the client, so…, Small, stable record of who this person was when the thread opened., _user_snapshot(), _card(), ChatIn, ChatResumeIn, _format_interrupt() (+22 more)

### Community 60 - "test_rerank.py"
Cohesion: 0.08
Nodes (29): The string the retrievers search for — not the string the model reads.…, (older, recent). `recent` goes to the model verbatim., retrieval_query(), split_horizons(), _is_capability(), _label(), Wrap a title into a mermaid label with <br/> breaks. Long titles are the norm…, Reranking, conversation context, buy-intent, and the learning path. These four… (+21 more)

### Community 61 - "test_providers.py"
Cohesion: 0.11
Nodes (24): active_backend(), backend_info(), Which backend the next call will use: "mesh" or "local"., What is actually serving embeddings, for /admin and startup logs., Remove <think> blocks from a chat completion, in place. Done here rather than…, strip_reasoning(), _Choice, _Err (+16 more)

### Community 62 - "DS"
Cohesion: 0.12
Nodes (29): _2e(), A1(), A2e(), B2e(), cl(), D2e(), DS(), F2e() (+21 more)

### Community 65 - "datetime"
Cohesion: 0.33
Nodes (3): Client clocks send tz-aware ISO ('...Z'); every other ts in the schema is a…, datetime, field_validator

### Community 77 - "tracker.js"
Cohesion: 0.35
Nodes (8): activeMs(), flush(), sampleScroll(), schedule(), scrollPercent(), sendDwell(), track(), uuid()

### Community 80 - "conftest.py"
Cohesion: 0.29
Nodes (7): db(), _guard_database_url(), fixture, pytest_sessionstart(), Start each run from an empty test database. Deleting the file up front rather…, Fail loudly if anything re-pointed the engine at the real database., Ensure the schema exists before a test that touches tables. Autouse, because…

### Community 82 - "Qt"
Cohesion: 0.16
Nodes (29): a_e(), aZ(), buildLookaheadForOptional(), Ci(), cm(), CZ(), e_e(), F5() (+21 more)

### Community 83 - "build_pathway_async"
Cohesion: 0.13
Nodes (27): build_pathway(), build_pathway_async(), _build_without_prereqs(), _capabilities(), _edge_label(), _final_gain(), _gain(), _goal_label() (+19 more)

### Community 84 - "auth/routes.py"
Cohesion: 0.14
Nodes (24): create_admin(), main(), make_admin(), python -m app.auth.cli — create or promote an admin. Registration always yields…, login(), login_form(), _login_response(), logout() (+16 more)

### Community 85 - "r8"
Cohesion: 0.09
Nodes (27): b8(), bh(), copy(), d3(), dTe(), DV(), dy(), gh() (+19 more)

### Community 86 - "mFe"
Cohesion: 0.14
Nodes (26): af(), Ai(), buildDuplicateFoundError(), buildEmptyRepetitionError(), dg(), $Fe(), fFe(), fOe() (+18 more)

### Community 87 - "update"
Cohesion: 0.10
Nodes (25): create(), createAsync(), createLangiumDocument(), createTextDocumentGetter(), en(), ensureBeforeEOL(), fce(), fromString() (+17 more)

### Community 88 - ".toString"
Cohesion: 0.10
Nodes (24): $9(), addDocument(), buildDocuments(), createDocument(), deleteDocument(), g8e(), gEe(), getBuildOptions() (+16 more)

### Community 89 - "indexOf"
Cohesion: 0.09
Nodes (25): bD(), $C(), c8e(), cTe(), eBe(), every(), findIndex(), getAstNode() (+17 more)

### Community 90 - "mesh_fallback_middleware"
Cohesion: 0.11
Nodes (21): build_chat_models(), mesh_fallback_middleware(), ModelRequest, wrap_model_call, Bridge between the existing OpenAI-SDK provider chain (app/agent/providers.py)…, (prompt, completion, total) tokens from the AIMessage LangChain returned, or…, One `ChatOpenAI` per provider tier, keyed the same as `providers.chain()`.…, Apply providers.strip_think_text() to the AIMessage LangChain returned. Same… (+13 more)

### Community 91 - "rerank.py"
Cohesion: 0.13
Nodes (22): Returns (parsed, model_used, fallback_used)., structured_call(), _course_text(), cross_encode_score(), _llm_rerank(), pair_features(), _profile_bonus(), Reranking for chat retrieval — the second stage after RRF fusion. Why a second… (+14 more)

### Community 92 - "setAttribute"
Cohesion: 0.09
Nodes (23): aA(), cy(), E7(), getAttribute(), getColor(), hA(), iA(), ic() (+15 more)

### Community 93 - "build"
Cohesion: 0.09
Nodes (23): bl(), build(), c3(), clamp(), Dp(), emitUpdate(), formatHsl(), g3() (+15 more)

### Community 94 - "chat.js"
Cohesion: 0.19
Nodes (19): addBot(), addInterrupt(), addOffer(), addPathway(), addUser(), appendCourseLink(), applyFull(), clearTyping() (+11 more)

### Community 95 - "lineTo"
Cohesion: 0.13
Nodes (21): att(), bezierCurveTo(), Bp(), brt(), closePath(), _drawToContext(), gMe(), itt() (+13 more)

### Community 96 - "constructor"
Cohesion: 0.10
Nodes (20): calculate(), constructor(), feed(), getComment(), initContentAssist(), initErrorHandler(), initGastRecorder(), initLexerAdapter() (+12 more)

### Community 97 - "qze"
Cohesion: 0.14
Nodes (20): addEntry(), co(), documentationTagRenderer(), getDocumentation(), jk(), JM(), lo(), nce() (+12 more)

### Community 98 - "visit"
Cohesion: 0.11
Nodes (20): _Be(), visit(), visitAlternative(), visitChildren(), visitDisjunction(), visitEndAnchor(), visitFlags(), visitGroup() (+12 more)

### Community 99 - "extract_facts"
Cohesion: 0.12
Nodes (18): compact(), extract_facts(), _norm_amount(), Conversation context: what the model sees, and what retrieval searches for. A…, Durable facts from the user's own turns, newest wins. Only `role == "user"` is…, Older turns → a few lines of durable fact. "" when there is nothing. This is…, parametrize, 3 years experience' must not become a ₹3 budget that filters the entire paid… (+10 more)

### Community 100 - "some"
Cohesion: 0.17
Nodes (19): blockTokens(), calculateHorizontalSpace(), calculateSpace(), calculateSpaceIfDrawnHorizontally(), calculateSpaceIfDrawnVertical(), calculateVerticalSpace(), getLabelDimension(), getRange() (+11 more)

### Community 101 - "htmlBuilder"
Cohesion: 0.12
Nodes (19): extend(), fontMetrics(), H9(), havingBaseSizing(), havingBaseStyle(), havingCrampedStyle(), havingSize(), havingStyle() (+11 more)

### Community 102 - "z_e"
Cohesion: 0.14
Nodes (18): B_e(), cu(), dZ(), F_e(), fZ(), G5(), G_e(), H_e() (+10 more)

### Community 103 - "_p"
Cohesion: 0.12
Nodes (10): bTe(), documentationLinkRenderer(), find(), findNameInGlobalScope(), findNameInPrecomputedScopes(), i8(), _p(), xTe() (+2 more)

### Community 104 - "test_tracker.js"
Cohesion: 0.11
Nodes (13): anchor, beaconEvents, body, card, clickEvents, depthEvents, detailEl, dwell (+5 more)

### Community 105 - "providers.py"
Cohesion: 0.18
Nodes (16): any_chat_provider(), available(), chain(), groq_client(), mesh_client(), ollama_client(), Which provider serves a call, and what happens when one stops working. The…, (provider, client, model) to try in order, for one kind of call. `kind` is… (+8 more)

### Community 106 - "splice"
Cohesion: 0.12
Nodes (17): addParents(), buildKeywordTokens(), buildTerminalTokens(), buildTokens(), distinct(), formLigatures(), g9(), H7e() (+9 more)

### Community 107 - "tn"
Cohesion: 0.15
Nodes (17): all(), createScope(), createScopeForNodes(), entriesGroupedByKey(), Fi(), findAllReferences(), findReferences(), getAllElements() (+9 more)

### Community 108 - "concat"
Cohesion: 0.13
Nodes (17): aV(), Bj(), buildLeftRecursionError(), concat(), cSe(), dequeue(), gIe(), hSe() (+9 more)

### Community 109 - "Gi"
Cohesion: 0.15
Nodes (17): computeExports(), computeExportsForNode(), computeLocalScopes(), exportNode(), getSource(), Gi(), NM(), notifyBuildPhase() (+9 more)

### Community 110 - "covers.py"
Cohesion: 0.19
Nodes (15): build_svg(), cover_path(), generate_all(), _glyphs(), palette(), Generated cover art for courses — `python -m app.catalog.covers`. Why generated…, One cover as an SVG string. 600×300, the 2:1 ratio the cards use., Generate a cover for every course in the catalog directory. Reads the JSON… (+7 more)

### Community 111 - "expandOnce"
Cohesion: 0.22
Nodes (16): consumeArg(), consumeArgs(), countExpansion(), expandAfterFuture(), expandMacro(), expandMacroAsText(), expandNextToken(), expandOnce() (+8 more)

### Community 112 - "score_intent"
Cohesion: 0.14
Nodes (14): _match_score(), Buy-intent detection — when to surface an offer inside a chat reply. The ask:…, Grade purchase intent for this turn. Returns a dict the route persists and the…, score_intent(), Enthusiasm plus an over-budget catalog is still not an offer., A pitch with nothing to point at is a banner ad., An explanation nobody can read is not an explanation — these end up in an admin…, test_bare_yes_needs_something_to_agree_with() (+6 more)

### Community 113 - "setData"
Cohesion: 0.13
Nodes (15): ait(), BVe(), FVe(), GVe(), IVe(), MVe(), OVe(), PVe() (+7 more)

### Community 114 - "parseInline"
Cohesion: 0.13
Nodes (15): autolink(), code(), codespan(), del(), em(), eo(), escape(), image() (+7 more)

### Community 115 - "performStartup"
Cohesion: 0.13
Nodes (15): dT(), eR(), flatMap(), getConfiguration(), getRootFolder(), includeEntry(), initialized(), initializeWorkspace() (+7 more)

### Community 116 - "Plan: compressed background context + a per-user knowledge store"
Cohesion: 0.13
Nodes (14): 10. Proactively ask how much time they want to invest, 1. Where the tokens actually go today, 2. Proposal, 2a. A stored "background brief", generated at write-time not read-time, 2b. A per-user knowledge store, separate from Chroma, 2c. Interaction with the usage guardrails just built, 3. Phased implementation, 4. Open questions (+6 more)

### Community 117 - "_P"
Cohesion: 0.13
Nodes (14): _P, Products may appear, but never before a capability., Product stand-in. These modules touch only these fields., Two steps both claiming the same skill is the dishonesty to avoid., A step whose every skill was covered still says something true., A long or pipe-bearing edge label stretches or breaks the diagram., A quote or newline in a title silently breaks the diagram in-browser., A term in the title says what the course IS; the same term in paragraph four of… (+6 more)

### Community 118 - "_date"
Cohesion: 0.25
Nodes (12): build_chunks(), chunk_metadata(), app/catalog/chunker.py — turns a curated course into Chroma chunks…, T1 filter surface, attached to every chunk of this course. Chroma rejects None…, build_catalog(), main(), python -m app.catalog.ingest — the RAG ingest path. data/data_1/*.json →…, Returns (courses, chunks). Pure — no I/O beyond reading the JSON files, so… (+4 more)

### Community 119 - "sync_sql.py"
Cohesion: 0.23
Nodes (12): as_product_row(), load_all(), app/catalog/loader.py — read data/data_1/*.json (one file per course, the…, Drop `_comment_*` keys, recursively. They are the audit trail (schema §7) and…, ▲B6: every prereq/related slug must exist. Fail loudly, before any writes.…, Project the rich course document onto the flat `products` SQL columns. The SQL…, strip_comments(), validate_graph() (+4 more)

### Community 120 - "checkIsTarget"
Cohesion: 0.23
Nodes (14): checkIsTarget(), visitAlternation(), visitOption(), visitRepetition(), visitRepetitionMandatory(), visitRepetitionMandatoryWithSeparator(), visitRepetitionWithSeparator(), walk() (+6 more)

### Community 121 - "atLeastOneInternal"
Cohesion: 0.15
Nodes (13): AT_LEAST_ONE(), AT_LEAST_ONE1(), AT_LEAST_ONE2(), AT_LEAST_ONE3(), AT_LEAST_ONE4(), AT_LEAST_ONE5(), AT_LEAST_ONE6(), AT_LEAST_ONE7() (+5 more)

### Community 122 - "getKeyForAutomaticLookahead"
Cohesion: 0.21
Nodes (13): consumeInternalError(), cstFinallyStateUpdate(), eFe(), getCurrRuleFullName(), getGAstProductions(), getKeyForAutomaticLookahead(), getLastExplicitRuleShortName(), isAtEndOfInput() (+5 more)

### Community 123 - "defineRule"
Cohesion: 0.15
Nodes (13): convert(), cstInvocationStateUpdate(), DEFINE_RULE(), defineRule(), e2(), Kse(), OVERRIDE_RULE(), RULE() (+5 more)

### Community 124 - "use"
Cohesion: 0.19
Nodes (13): Dc(), dse(), fse(), Gbe(), gR(), GT(), iFe(), pse() (+5 more)

### Community 125 - "freshness.py"
Cohesion: 0.23
Nodes (11): freshness_label(), _future_cohort(), _parse(), app/catalog/freshness.py — deterministic live-vs-recorded and recency scoring…, Score the *meaningful* date for this course's mode, not one global rule. For a…, A short phrase the generate node may put in front of a user. Emits a date ONLY…, Dates arrive as ISO strings from JSON. Anything unparseable is treated as…, format.cohort_start, but only if it is genuinely ahead of us. Schema §3 forbids… (+3 more)

### Community 126 - "performSelfAnalysis"
Cohesion: 0.23
Nodes (12): buildEmptyAlternationError(), computeLookaheadFunc(), disableRecording(), n2(), performSelfAnalysis(), preComputeLookaheadFunctions(), qse(), r2() (+4 more)

### Community 127 - "clear"
Cohesion: 0.18
Nodes (12): cancel(), clear(), dispose(), g1(), getDefaultConfig(), getDefaultData(), getDefaultThemeConfig(), m1() (+4 more)

### Community 128 - "subruleInternal"
Cohesion: 0.18
Nodes (12): cstPostNonTerminal(), SUBRULE1(), SUBRULE2(), SUBRULE3(), SUBRULE4(), SUBRULE5(), SUBRULE6(), SUBRULE7() (+4 more)

### Community 129 - "manyInternal"
Cohesion: 0.17
Nodes (12): many(), MANY1(), MANY2(), MANY3(), MANY4(), MANY5(), MANY6(), MANY7() (+4 more)

### Community 130 - "optionInternal"
Cohesion: 0.17
Nodes (12): option(), OPTION1(), OPTION2(), OPTION3(), OPTION4(), OPTION5(), OPTION6(), OPTION7() (+4 more)

### Community 131 - "orInternal"
Cohesion: 0.17
Nodes (12): or(), OR1(), OR2(), OR3(), OR4(), OR5(), OR6(), OR7() (+4 more)

### Community 132 - "Deterministic Fusion Ranking (RERANK_MODE=fusion)"
Cohesion: 0.20
Nodes (12): Canonical Category Set, The Ladder (schema definition) — prereq_ids / related_ids, Nine Required Course Fields, slug — the stable identity, Category Diversity Cap — 3 of 5, Deterministic Fusion Ranking (RERANK_MODE=fusion), The Ladder — prereq_ids / related_ids, −0.15 Recency Penalty and Conversion Exclusion (+4 more)

### Community 133 - "data_1/validate_seed.py"
Cohesion: 0.24
Nodes (11): check_content(), check_freshness(), check_pricing(), check_schema(), load_courses(), main(), Schema §3/§6 — the live-vs-recorded and recency inputs. A past date in…, Schema §1/§4 — the retrieval surface. (+3 more)

### Community 134 - "atLeastOneSepFirstInternal"
Cohesion: 0.18
Nodes (11): AT_LEAST_ONE_SEP(), AT_LEAST_ONE_SEP1(), AT_LEAST_ONE_SEP2(), AT_LEAST_ONE_SEP3(), AT_LEAST_ONE_SEP4(), AT_LEAST_ONE_SEP5(), AT_LEAST_ONE_SEP6(), AT_LEAST_ONE_SEP7() (+3 more)

### Community 135 - "getLaFuncFromCache"
Cohesion: 0.27
Nodes (11): atLeastOneInternalLogic(), atLeastOneSepFirstInternalLogic(), attemptInRepetitionRecovery(), doSingleRepetition(), getLaFuncFromCache(), getLexerPosition(), manyInternalLogic(), manySepFirstInternalLogic() (+3 more)

### Community 136 - "enableRecording"
Cohesion: 0.20
Nodes (11): atLeastOneInternalRecord(), atLeastOneSepFirstInternalRecord(), consumeInternalRecord(), enableRecording(), manyInternalRecord(), manySepFirstInternalRecord(), mk(), optionInternalRecord() (+3 more)

### Community 137 - "lex"
Cohesion: 0.18
Nodes (11): cj(), _De(), lex(), lexer(), Lxe(), Nb(), U7e(), uj() (+3 more)

### Community 138 - "jo"
Cohesion: 0.18
Nodes (11): createDehyrationContext(), createDescription(), createDescriptions(), createGrammarElementIdMap(), createHydrationContext(), getGrammarElement(), getGrammarElementId(), getTokenType() (+3 more)

### Community 139 - "manySepFirstInternal"
Cohesion: 0.18
Nodes (11): MANY_SEP(), MANY_SEP1(), MANY_SEP2(), MANY_SEP3(), MANY_SEP4(), MANY_SEP5(), MANY_SEP6(), MANY_SEP7() (+3 more)

### Community 140 - "C1e"
Cohesion: 0.20
Nodes (7): A1e(), Ay(), C1e(), Ey(), GA(), n1e(), vB()

### Community 141 - "Settings"
Cohesion: 0.22
Nodes (4): True when a real send is possible. Absence of credentials is a supported mode,…, Whether embeddings are possible at all. Distinct from `use_mesh`, which several…, Settings, BaseSettings

### Community 142 - "subrule"
Cohesion: 0.28
Nodes (9): after(), assign(), assignWithoutOverride(), before(), fM(), isRecording(), performSubruleAssignment(), subrule() (+1 more)

### Community 143 - "Ym"
Cohesion: 0.22
Nodes (9): buildKeywordPattern(), buildKeywordToken(), buildTerminalToken(), findLongerAlt(), jN(), regexPatternFunction(), requiresCustomPattern(), XN() (+1 more)

### Community 144 - "tokenizeInternal"
Cohesion: 0.22
Nodes (9): buildUnableToPopLexerModeMessage(), buildUnexpectedCharactersMessage(), chopInput(), computeNewColumn(), handleModes(), tokenize(), tokenizeInternal(), updateLastIndex() (+1 more)

### Community 145 - "link"
Cohesion: 0.31
Nodes (9): createLinkingError(), doLink(), getCandidate(), getElement(), getLinkedNode(), hasDocument(), link(), loadAstNode() (+1 more)

### Community 146 - "ei"
Cohesion: 0.25
Nodes (9): Ct(), deserialize(), ei(), fromModel(), getRefNode(), linkNode(), of(), reviveReference() (+1 more)

### Community 147 - "g"
Cohesion: 0.25
Nodes (6): addHiddenToken(), addHiddenTokens(), buildLeafNode(), g(), $m(), s

### Community 148 - "flat"
Cohesion: 0.25
Nodes (8): allElements(), flat(), getFileDescriptions(), mze(), pze(), toTokenTypeDictionary(), values(), walkTokens()

### Community 149 - "e0"
Cohesion: 0.25
Nodes (8): BBe(), e0(), fR(), Iae(), oBe(), pBe(), walkProdRef(), walkTerminal()

### Community 150 - "Freshness Scoring Specification (schema §6)"
Cohesion: 0.32
Nodes (8): cohort_start vs cohort_start_stale — the two date fields, effective_mode() — returns demoted mode plus reason, The format block — delivery and freshness fields, Freshness Scoring Specification (schema §6), mode — live / hybrid / self-paced / recorded, score_freshness() — number plus explain dict and label, The Demotion Rule — live with no future cohort scores as recorded, Freshness Scoring — live-vs-recorded axis

### Community 151 - "accept"
Cohesion: 0.33
Nodes (7): accept(), bFe(), bSe(), buildAlternationPrefixAmbiguityError(), ng(), resolveRefs(), zse()

### Community 152 - "acquireParserWorker"
Cohesion: 0.33
Nodes (7): acquireParserWorker(), initializeWorkers(), lock(), onBuildPhase(), onCancellationRequested(), onReady(), waitUntil()

### Community 153 - "aO"
Cohesion: 0.29
Nodes (7): addClass(), addPoints(), aO(), $he(), $Ve(), Vhe(), VVe()

### Community 154 - "_comment_* fields — the curation audit trail"
Cohesion: 0.29
Nodes (7): _comment_* fields — the curation audit trail, level — the fusion-match anchor, price: null Is Legitimate (coupon volatility), Price Bands — defined, not vibes, Computed Confidence — weighted-additive, never asked of the model, The Recommendation Card, strip_comments() — _comment_* audit trail removal

### Community 155 - "tryInRepetitionRecovery"
Cohesion: 0.40
Nodes (6): BACKTRACK(), exportLexerState(), importLexerState(), reloadRecogState(), saveRecogState(), tryInRepetitionRecovery()

### Community 156 - "reset_breakers"
Cohesion: 0.40
Nodes (5): Test hook. Module-level state would otherwise leak between tests., reset_breakers(), _clean_breakers(), fixture, Cooldowns are module-level state and would leak between tests.

### Community 157 - "_7"
Cohesion: 0.40
Nodes (5): _7(), L7(), N5e(), O5e(), z4()

### Community 158 - "kFe"
Cohesion: 0.40
Nodes (5): init(), input(), kFe(), reset(), resetLexerState()

### Community 159 - "lAe"
Cohesion: 0.50
Nodes (5): L5(), lAe(), O9(), pZ(), Sd()

### Community 160 - "_cache_key"
Cohesion: 0.50
Nodes (4): _cache_key(), Cache key includes the backend. Keying on text alone was the bug waiting to…, The dimension trap: a 1536-dim Mesh vector served to a 768-dim nomic collection…, test_embedding_cache_is_keyed_per_backend()

### Community 161 - "l8e"
Cohesion: 0.50
Nodes (4): a8e(), l8e(), o8e(), s8e()

### Community 162 - "getAllSubTypes"
Cohesion: 0.50
Nodes (4): computeIsSubtype(), getAllSubTypes(), getAllTypes(), isSubtype()

### Community 163 - "mO"
Cohesion: 0.50
Nodes (4): eue(), mO(), rue(), tUe()

### Community 164 - "Ul"
Cohesion: 0.50
Nodes (4): Hqe(), Ul(), Uqe(), Vqe()

### Community 165 - "table"
Cohesion: 0.50
Nodes (4): jX(), table(), tablecell(), tablerow()

### Community 166 - "D8"
Cohesion: 0.67
Nodes (3): _8(), D8(), rTe()

### Community 167 - "jge"
Cohesion: 0.67
Nodes (3): B6(), jge(), P6()

### Community 168 - "BF"
Cohesion: 0.67
Nodes (3): BF(), bxe(), xxe()

### Community 169 - "cve"
Cohesion: 0.67
Nodes (3): cve(), JB(), lve()

### Community 170 - "q6"
Cohesion: 0.67
Nodes (3): dye(), E1(), q6()

### Community 171 - "rYe"
Cohesion: 0.67
Nodes (3): eP(), rYe(), tP()

### Community 172 - "getAllTags"
Cohesion: 0.67
Nodes (3): getAllTags(), getTag(), getTags()

### Community 173 - "HCe"
Cohesion: 0.67
Nodes (3): HCe(), MX(), wCe()

### Community 174 - "zB"
Cohesion: 0.67
Nodes (3): hrt(), T1(), zB()

### Community 175 - "hy"
Cohesion: 0.67
Nodes (3): hy(), p7(), R7()

### Community 176 - "Y6"
Cohesion: 0.67
Nodes (3): hye(), uye(), Y6()

### Community 177 - "O7"
Cohesion: 0.67
Nodes (3): I7(), O7(), z5e()

### Community 178 - "qR"
Cohesion: 0.67
Nodes (3): isEpsilon(), qR(), yr()

### Community 179 - "Pn"
Cohesion: 0.67
Nodes (3): kA(), Pn(), R3()

### Community 180 - "rV"
Cohesion: 0.67
Nodes (3): rV(), v7(), y7()

## Knowledge Gaps
- **222 isolated node(s):** `$schema`, `$id`, `title`, `description`, `type` (+217 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **38 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Product` connect `Product` to `admin/routes.py`, `generate.py`, `User`, `test_outbox.py`, `agent.py`, `chat/routes.py`, `test_mail.py`, `score_intent`, `build_pathway_async`, `embed_batch`, `sync_sql.py`, `test_chat.py`, `models.py`, `rerank.py`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Why does `load_all()` connect `sync_sql.py` to `_date`, `catalogue.py`, `covers.py`?**
  _High betweenness centrality (0.007) - this node is a cross-community bridge._
- **Why does `addBot()` connect `chat.js` to `push`, `insert`?**
  _High betweenness centrality (0.007) - this node is a cross-community bridge._
- **Are the 173 inferred relationships involving `r()` (e.g. with `_4()` and `a()`) actually correct?**
  _`r()` has 173 INFERRED edges - model-reasoned connections that need verification._
- **Are the 149 inferred relationships involving `n()` (e.g. with `_4()` and `ACTION()`) actually correct?**
  _`n()` has 149 INFERRED edges - model-reasoned connections that need verification._
- **Are the 131 inferred relationships involving `a()` (e.g. with `addBot()` and `addPathway()`) actually correct?**
  _`a()` has 131 INFERRED edges - model-reasoned connections that need verification._
- **Are the 87 inferred relationships involving `l()` (e.g. with `renderBody()` and `_4()`) actually correct?**
  _`l()` has 87 INFERRED edges - model-reasoned connections that need verification._