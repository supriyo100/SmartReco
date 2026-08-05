"""7 personas (v1's four + DeepSeek's three, ▲A15). Replay as event streams,
then measure: grounding rate (target 100%), category precision@5 (>0.8),
LLM calls/100 events (<6), cache hit rate (>60%), latencies,
plus ▲B8: Coverage (% of catalog ever recommended across all personas)
and Diversity@5 (mean distinct categories per rec set).
Every README number: measured or labeled — never invented.
two_interests directly validates per-interest multi-query + RRF —
a blended-query design fails it.
"""
PERSONAS = {
    "career_switcher": {}, "advanced_practitioner": {},
    "budget_beginner": {}, "no_intent_browser": {},
    "confused": {},          # mixed searches, consistent dwell → infer true interest
    "price_sensitive": {},   # expects low price_band recs
    "two_interests": {},     # expects BOTH categories represented
}
