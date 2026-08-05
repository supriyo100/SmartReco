"""Trigger policy — arch v1 §5.2, unchanged in v2. THIS IS THE PLANNER.
Run agent iff: cosine(old,new) > threshold | >=N significant events |
rec stale + user active | high-intent event. Suppress: 90s debounce,
per-user asyncio.Lock + DB running flag, cold-start floor (<3 events →
trending-within-observed-signal, never generic popular).
"""
