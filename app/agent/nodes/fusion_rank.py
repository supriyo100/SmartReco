"""fusion_rank node — implement per SmartReco_Architecture_v2_FINAL.md §3.
Happy path is TWO LLM calls total: analyze + generate.
fusion_rank notes:
  ▲A14+B2 deterministic: 0.45*norm(rrf) + 0.3*interest_match
  + 0.1*rating_prior + 0.1*popularity(log view-count from events GROUP BY)
  + 0.05*graph_adjacency(related/prereq of viewed). Then:
  ▲B4 EXCLUDE products with a conversion event for this user;
      -0.15 penalty for items in the user's previous current rec.
  ▲B3 diversity cap: <=3 of final 5 from one category. Top 8 to grade.
  RERANK_MODE flag can swap to cross_encoder or llm.
"""
