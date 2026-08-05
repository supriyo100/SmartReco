"""retrieve node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.
retrieve notes:
  Per-interest Chroma query + FTS5 MATCH query, merge with RRF (k=60).
  Metadata filter: is_active, price ceiling, level within one step.
"""
