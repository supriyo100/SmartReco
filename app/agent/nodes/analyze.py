"""analyze node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.
analyze notes:
  LLM #1 via mesh.structured_call (fallback chain built in).
  Input includes BOTH horizons from scorer: interests (long) + interests_short.
  Output: {intent, categories[], level, budget_band, stage, retrieval_queries[<=3]}
"""
