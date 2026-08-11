# Graph Report - .  (2026-08-06)

## Corpus Check
- 91 files · ~51,138 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 700 nodes · 1050 edges · 82 communities (68 shown, 14 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 44 edges (avg confidence: 0.84)
- Token cost: 174,014 input · 0 output

## Community Hubs (Navigation)
- Chunking and Freshness Scoring
- Admin Telemetry and Catalog UI
- Admin CRUD Routes
- Course JSON Schema Standard
- Curation Tracker
- JSON Schema Root Structure
- App Entrypoint and Public Routes
- Request Identity and Auth Deps
- Config, DB Session, Nightly Jobs
- README Architecture Narrative
- Schema: Format and Access Fields
- DB Models and Init
- Deterministic Interest Scoring
- Schema: Card and Project Fields
- Schema: Category Enum
- Schema: Objectives and Ladder Arrays
- Schema: Price and Rating
- Schema: Module Groups
- Schema: Level Range and Refs
- Embedding Batch and Seeding
- Event Tracking Ingest
- LangGraph Agent and Mesh Config
- Mesh API Gateway
- Schema: Cohort Date Fields
- Schema: Source Platform Enum
- CI Workflow and OIDC
- Interest Scorer and Fingerprint
- Hybrid Retrieval and Evaluation
- Schema: Enrollment Status Enum
- Schema: Delivery Mode Enum
- Schema: Price Band Enum
- Auth Templates and Role Gate
- Schema: Level Enum
- Dependencies and Make Targets
- Schema: Currency Enum
- Schema: Objectives Array
- Schema: Format Required Fields
- Schema: Source URLs
- Schema: Related IDs
- Schema: Skills Array
- Experiment Chunker Variant
- Schema: Cohort Start
- Schema: Content Updated
- Schema: Module Groups Limit
- Schema: Overview Field
- Schema: Published At
- Schema: Slug Pattern
- Schema: Title Field
- Schema: Is Free Flag
- Schema: Mentors List
- Schema: Price Note
- Schema: Schedule Field
- Course File Loader
- Experiment Course Loader
- LangGraph Assembly
- Analyze Node
- Fusion Rank Node
- Generate Node
- Grade Node
- Retrieve Node
- Validate Node
- Trigger Policy Planner
- AWS Model Training Concepts
- Evaluation Personas
- Setup Script
- SmartReco Project Root

## God Nodes (most connected - your core abstractions)
1. `score_freshness()` - 29 edges
2. `render()` - 22 edges
3. `course()` - 20 edges
4. `_date()` - 16 edges
5. `build_catalogue()` - 15 edges
6. `Base` - 12 edges
7. `User` - 12 edges
8. `effective_mode()` - 11 edges
9. `required` - 10 edges
10. `enum` - 10 edges

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

## Communities (82 total, 14 thin omitted)

### Community 0 - "Chunking and Freshness Scoring"
Cohesion: 0.07
Nodes (63): build_chunks(), chunk_metadata(), generate_context(), app/catalog/chunker.py — turns a curated course into Chroma chunks…, T1 filter surface, attached to every chunk of this course. Chroma rejects None…, Tier 3 — compressed injection, ~130 tokens/candidate (arch D2). Ships the top-3…, effective_mode(), freshness_label() (+55 more)

### Community 1 - "Admin Telemetry and Catalog UI"
Cohesion: 0.05
Nodes (53): Agent Run Telemetry Record, Admin Agent Runs Table, LLM Call Efficiency Metric (arch 1.2), Admin Dashboard, Catalog Ingest Action, Pending Vector Sync Outbox, Course Ladder (prereq / related slugs), Admin Product Form (+45 more)

### Community 2 - "Admin CRUD Routes"
Cohesion: 0.09
Nodes (47): agent_runs(), create_product(), dashboard(), delete_product(), edit_product_form(), list_products(), new_product_form(), get (+39 more)

### Community 3 - "Course JSON Schema Standard"
Cohesion: 0.06
Nodes (49): Canonical Category Set, career_roles — a card field, not an embedded field, cohort_start vs cohort_start_stale — the two date fields, Experiment/collect_helper.py — page and PDF text dumper, _comment_* fields — the curation audit trail, The Corroboration Rule (schema statement), SmartReco Course JSON Standard v1.0, course.schema.json — structural schema (+41 more)

### Community 4 - "Curation Tracker"
Cohesion: 0.13
Nodes (24): _add_chunk_share(), build_catalogue(), _comment_count(), _counts(), _course_flags(), _flag(), _is_placeholder_url(), main() (+16 more)

### Community 5 - "JSON Schema Root Structure"
Cohesion: 0.09
Nodes (21): additionalProperties, allOf, description, type, description, $id, patternProperties, ^_comment (+13 more)

### Community 6 - "App Entrypoint and Public Routes"
Cohesion: 0.16
Nodes (15): check_models_at_startup(), Log structured-output support per configured model. Don't assume., Public catalog browsing: landing, search, course detail (arch §1.1 route map).…, healthz(), lifespan(), get, build_scheduler(), asyncio queue + batch writer. Handler does validation + put — nothing else. (+7 more)

### Community 7 - "Request Identity and Auth Deps"
Cohesion: 0.18
Nodes (15): current_user(), identity_middleware(), Request, Request-scoped identity: middleware + the two FastAPI dependencies.…, Populate request.state.{user_id,role,session_id} for every request. The…, The logged-in User row, or None. Re-reads the DB so a deleted or demoted user…, require_admin(), require_user() (+7 more)

### Community 8 - "Config, DB Session, Nightly Jobs"
Cohesion: 0.16
Nodes (11): Settings, Nightly job (▲A16): checkpoint + truncate the -wal file., _set_sqlite_pragma(), wal_checkpoint(), nightly_maintenance(), APScheduler jobs. SINGLE WORKER ONLY (uvicorn --workers 1) — arch trap #3., ▲A16: WAL checkpoint + orphan event cleanup + SQL↔Chroma reconcile., # TODO: DELETE events WHERE user_id IS NULL AND ts < now-90d (+3 more)

### Community 9 - "README Architecture Narrative"
Cohesion: 0.14
Nodes (15): tracker.js deferred script include, How Recommendations Are Grounded (README §4), SmartReco README Outline, APScheduler Job Schedule, Deliberately Not Built — rejected ideas with stated reasons, Digest Email — bonus tier, first to be cut, Dropping Loudly Beats Blocking Silently, Dual-Write — SQLite plus Chroma on every product mutation (+7 more)

### Community 10 - "Schema: Format and Access Fields"
Cohesion: 0.14
Nodes (14): description, type, description, description, type, description, type, properties (+6 more)

### Community 11 - "DB Models and Init"
Cohesion: 0.26
Nodes (10): python -m app.db.init_db — create schema, FTS5, triggers, indexes., AgentRun, Base, DigestLog, EmbeddingCache, Event, Recommendation, UserProfile (+2 more)

### Community 12 - "Deterministic Interest Scoring"
Cohesion: 0.22
Nodes (13): Browsing-carries-over copy on the signup page, How We Avoid Wasteful LLM Calls (README §3), Cold-Start Floor — trending within observed signal, The Planner Is Deliberately Not an LLM, Dual-Horizon Deterministic Interest Scorer, Efficiency Thesis — LLM runs only when behavior materially changed, events table — behavioral log, Exponential-Decay Feedback Loop (rec_click / rec_dismiss) (+5 more)

### Community 13 - "Schema: Card and Project Fields"
Cohesion: 0.15
Nodes (13): description, items, type, default, type, type, properties, career_roles (+5 more)

### Community 14 - "Schema: Category Enum"
Cohesion: 0.15
Nodes (13): description, enum, type, category, Agentic AI, Analytics, Cloud, Computer Vision (+5 more)

### Community 15 - "Schema: Objectives and Ladder Arrays"
Cohesion: 0.15
Nodes (13): type, description, items, type, items, type, description, items (+5 more)

### Community 16 - "Schema: Price and Rating"
Cohesion: 0.20
Nodes (12): type, description, minimum, type, price, rating, description, maximum (+4 more)

### Community 17 - "Schema: Module Groups"
Cohesion: 0.17
Nodes (12): minLength, type, properties, required, items, items, minItems, type (+4 more)

### Community 18 - "Schema: Level Range and Refs"
Cohesion: 0.18
Nodes (11): $ref, description, items, maxItems, minItems, type, level_range, secondary_categories (+3 more)

### Community 19 - "Embedding Batch and Seeding"
Cohesion: 0.29
Nodes (9): embed_batch(), _h(), ▲A5: one API call for the whole batch, with read-through cache., Product, embedding_text(), main(), python -m app.db.seed — load seed/products.json into SQL + Chroma. Uses…, ▲B6: every prereq/related slug must exist. Fail loudly, before any writes. (+1 more)

### Community 20 - "Event Tracking Ingest"
Cohesion: 0.27
Nodes (9): stats(), event_stats(), EventBatch, ingest(), get, post, Request, TrackedEvent (+1 more)

### Community 21 - "LangGraph Agent and Mesh Config"
Cohesion: 0.20
Nodes (10): analyze_behavior node (LLM #1), Build Order and Cut Order, Configuration — .env keys and model selection, embed_batch(texts) — single POST batch embedding, embedding_cache — text_hash to vector, Fence-Stripping JSON Parser, LangGraph Recommendation Agent — two LLM calls happy path, Mesh API Integration Layer (+2 more)

### Community 22 - "Mesh API Gateway"
Cohesion: 0.33
Nodes (8): _chat(), _parse_json(), Single gateway for all Mesh API calls: chat, structured chat, embeddings. ▲A2…, ▲B1 last line of defense: some models return prose-wrapped or fence-wrapped…, Returns (parsed, model_used, fallback_used)., Persuasive generation under a strict schema (arch v1 §5.4 generate)., structured_call(), writer_call()

### Community 23 - "Schema: Cohort Date Fields"
Cohesion: 0.22
Nodes (9): description, pattern, type, type, description, type, cohort_start_stale, curriculum_version (+1 more)

### Community 24 - "Schema: Source Platform Enum"
Cohesion: 0.22
Nodes (9): source_platform, default, enum, type, coursera, other, own_site, udemy (+1 more)

### Community 25 - "CI Workflow and OIDC"
Cohesion: 0.25
Nodes (8): Platform CI Workflow Setup Instructions, Throwaway Spend-Capped MESH_API_KEY Policy, Concurrency cancel-in-progress group, Download checks step, OIDC_AUDIENCE opaque identifier, Request GitHub OIDC token step, Run checks step, SmartReco Checks Workflow

### Community 26 - "Interest Scorer and Fingerprint"
Cohesion: 0.29
Nodes (6): fingerprint(), Deterministic interest scorer — the cheap layer that gates the agent. ▲A7 dual…, events: [{event_type, category, ts, dwell_ms?, meta?}] → dict of vectors., Coarse on purpose: top-3 merged categories at 0.1 buckets + band + stage., _score(), score_interests()

### Community 27 - "Hybrid Retrieval and Evaluation"
Cohesion: 0.25
Nodes (8): Header search form (GET /search), agent_runs — observability table, Evaluation Harness — seven personas and ablations, Hybrid Retrieval with Reciprocal Rank Fusion, Every Number Measured or Labeled, Per-Interest Query (not blended centroid), products_fts — FTS5 trigger-maintained virtual table, two_interests eval persona

### Community 28 - "Schema: Enrollment Status Enum"
Cohesion: 0.25
Nodes (8): default, enum, type, enrollment_status, closed, closing_soon, open, waitlist

### Community 29 - "Schema: Delivery Mode Enum"
Cohesion: 0.25
Nodes (8): description, enum, type, mode, hybrid, live, recorded, self-paced

### Community 30 - "Schema: Price Band Enum"
Cohesion: 0.25
Nodes (8): description, enum, type, price_band, free, high, low, mid

### Community 31 - "Auth Templates and Role Gate"
Cohesion: 0.48
Nodes (7): login.html — login form template, register.html — registration form template, base.html — site layout shell, Nav role gate — admin link shown when role == 'admin', Signed-Cookie Email/Password Authentication, require_admin dependency, Two Roles, One Column (users.role)

### Community 32 - "Schema: Level Enum"
Cohesion: 0.29
Nodes (7): description, enum, type, level, advanced, beginner, intermediate

### Community 33 - "Dependencies and Make Targets"
Cohesion: 0.29
Nodes (7): bcrypt Direct — passlib 1.7.4 unmaintained, Dev Dependency Set (pytest, ruff), Runtime Dependency Set, app.db.init_db — schema creation as migration story, Make Targets — init, validate, catalogue, ingest, dev, test, lint, Server-Rendered FastAPI Web Application, SmartReco Course Recommendation Platform

### Community 34 - "Schema: Currency Enum"
Cohesion: 0.33
Nodes (6): default, enum, type, currency, INR, USD

### Community 35 - "Schema: Objectives Array"
Cohesion: 0.33
Nodes (6): minLength, description, items, minItems, type, objectives

### Community 36 - "Schema: Format Required Fields"
Cohesion: 0.40
Nodes (5): description, required, type, format, mode

### Community 37 - "Schema: Source URLs"
Cohesion: 0.40
Nodes (5): format, source_urls, description, items, type

### Community 38 - "Schema: Related IDs"
Cohesion: 0.40
Nodes (5): related_ids, description, items, maxItems, type

### Community 39 - "Schema: Skills Array"
Cohesion: 0.40
Nodes (5): skills, description, items, minItems, type

### Community 40 - "Experiment Chunker Variant"
Cohesion: 0.40
Nodes (3): generate_context(), app/catalog/chunker.py — turns a seed product into Chroma chunks (arch D2, Tier…, Tier 3 — compressed injection, ~120 tokens/candidate (arch D2). Ships the top-3…

### Community 41 - "Schema: Cohort Start"
Cohesion: 0.50
Nodes (4): description, format, pattern, cohort_start

### Community 42 - "Schema: Content Updated"
Cohesion: 0.50
Nodes (4): description, pattern, type, content_updated

### Community 43 - "Schema: Module Groups Limit"
Cohesion: 0.50
Nodes (4): description, maxItems, type, module_groups

### Community 44 - "Schema: Overview Field"
Cohesion: 0.50
Nodes (4): description, minLength, type, overview

### Community 45 - "Schema: Published At"
Cohesion: 0.50
Nodes (4): published_at, description, pattern, type

### Community 46 - "Schema: Slug Pattern"
Cohesion: 0.50
Nodes (4): slug, description, pattern, type

### Community 47 - "Schema: Title Field"
Cohesion: 0.50
Nodes (4): title, description, minLength, type

### Community 48 - "Schema: Is Free Flag"
Cohesion: 0.67
Nodes (3): description, type, is_free

### Community 49 - "Schema: Mentors List"
Cohesion: 0.67
Nodes (3): items, type, mentors

### Community 50 - "Schema: Price Note"
Cohesion: 0.67
Nodes (3): description, type, price_note

### Community 51 - "Schema: Schedule Field"
Cohesion: 0.67
Nodes (3): schedule, description, type

## Knowledge Gaps
- **162 isolated node(s):** `$schema`, `$id`, `title`, `description`, `type` (+157 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `properties` connect `Schema: Card and Project Fields` to `JSON Schema Root Structure`, `Schema: Category Enum`, `Schema: Objectives and Ladder Arrays`, `Schema: Price and Rating`, `Schema: Level Range and Refs`, `Schema: Cohort Date Fields`, `Schema: Source Platform Enum`, `Schema: Price Band Enum`, `Schema: Level Enum`, `Schema: Currency Enum`, `Schema: Objectives Array`, `Schema: Format Required Fields`, `Schema: Source URLs`, `Schema: Related IDs`, `Schema: Skills Array`, `Schema: Module Groups Limit`, `Schema: Overview Field`, `Schema: Slug Pattern`, `Schema: Title Field`, `Schema: Is Free Flag`, `Schema: Mentors List`, `Schema: Price Note`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `properties` connect `Schema: Format and Access Fields` to `Schema: Format Required Fields`, `Schema: Cohort Start`, `Schema: Content Updated`, `Schema: Published At`, `Schema: Schedule Field`, `Schema: Cohort Date Fields`, `Schema: Enrollment Status Enum`, `Schema: Delivery Mode Enum`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Why does `format` connect `Schema: Format Required Fields` to `Schema: Format and Access Fields`, `Schema: Card and Project Fields`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **What connects `$schema`, `$id`, `title` to the rest of the system?**
  _162 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Chunking and Freshness Scoring` be split into smaller, more focused modules?**
  _Cohesion score 0.06672519754170325 - nodes in this community are weakly interconnected._
- **Should `Admin Telemetry and Catalog UI` be split into smaller, more focused modules?**
  _Cohesion score 0.05152394775036284 - nodes in this community are weakly interconnected._
- **Should `Admin CRUD Routes` be split into smaller, more focused modules?**
  _Cohesion score 0.08549019607843138 - nodes in this community are weakly interconnected._