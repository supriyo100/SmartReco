"""generate node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.
generate notes:
  LLM #2 via mesh.writer_call. Narrative must reference both horizons.
  Per item: reason + hook. Confidence attached AFTER in Python (▲A9) — never
  asked of the LLM.
"""
