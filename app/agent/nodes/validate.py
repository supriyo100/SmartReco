"""validate node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.
validate notes:
  Pure Python. IDs ⊆ retrieved set, active, <=5, deduped, reason<=220ch.
  One regeneration on violation, then drop offenders. The grounding guarantee.
"""
