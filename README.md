# SmartReco — Behavioral AI Recommendation Agent

> Skeleton README — fill Aug 8 PM per arch v1 §11 outline. Section 3 leads.

1. What it is + 60s demo GIF
2. Architecture diagram
3. **How we avoid wasteful LLM calls** — deterministic dual-horizon scorer,
   fingerprint gating, trigger policy, three caches. MEASURED numbers here.
4. **How recommendations are grounded** — dual-write, hybrid retrieval, validate node
5. Agent workflow (2-LLM happy path) + LangSmith trace screenshot
6. Event tracking design + p95 number
7. Bonus features implemented — name all four
8. Evaluation results + ablation (single-run, labeled directional)
9. Setup & run (clean-clone verified)
10. Trade-offs & future work (planner-LLM analysis, knowledge graph, learned
    ranker, Redis Streams, collaborative filtering, A/B over Mesh fan-out)
