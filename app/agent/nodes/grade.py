"""grade node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.
grade notes:
  ▲A3 deterministic coverage FIRST (top-5 cover inferred cats?).
  ratio==1 → generate. ratio==0 → refine (1 loop max). else → llm grade (rare).
"""
