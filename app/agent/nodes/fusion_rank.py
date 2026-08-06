"""fusion_rank node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.

fusion_rank notes:
  ▲A14+B2 deterministic weighted fusion. v2.3 adds the freshness term
  (data/COURSE_SCHEMA.md §6) at 0.10, taken proportionally from the existing
  terms so the vector still sums to 1.0:

      0.40 * norm(rrf)
    + 0.28 * interest_match
    + 0.10 * freshness          ← NEW: live-vs-recorded + recency
    + 0.09 * popularity          (log view-count from events GROUP BY)
    + 0.08 * rating_prior
    + 0.05 * graph_adjacency     (related/prereq of viewed)
    ─────
      1.00

  Freshness comes precomputed on every chunk's Chroma metadata (`freshness`,
  written by catalog/chunker.py at ingest), so ranking never parses a date:
      from app.catalog.freshness import score_freshness   # for live re-scoring
      fresh = hit["metadata"]["freshness"]                 # normal path

  Why it earns 0.10: users want live and they want recent, and the embedding
  cannot see either — two cohorts of the same syllabus, one starting in four
  weeks and one finished eighteen months ago, have near-identical text. It is a
  fact about time, so it is scored in Python like every other term here, never
  asked of an LLM. Re-score (don't trust stale metadata) if the index is older
  than a day: a cohort passes without anyone editing a file.

  Then:
  ▲B4 EXCLUDE products with a conversion event for this user;
      -0.15 penalty for items in the user's previous current rec.
  ▲B3 diversity cap: <=3 of final 5 from one category. Top 8 to grade.
  RERANK_MODE flag can swap to cross_encoder or llm.
"""
