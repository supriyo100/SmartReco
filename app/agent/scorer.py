"""Deterministic interest scorer — the cheap layer that gates the agent.

▲A7 dual horizon: λ_long = ln2/72h (enduring interests), λ_short = ln2/6h
(current session intent). merged = 0.6·long + 0.4·short. Fingerprint over
merged. ▲A8 feedback events reweight the same model — this is the feedback
loop, no extra ML.
"""
import hashlib
import math
from collections import defaultdict
from datetime import datetime, timezone

WEIGHTS = {
    "page_view": 1.0, "product_view": 2.0, "search": 3.0,
    "search_result_click": 3.0, "category_filter": 1.5, "scroll_depth_75": 1.0,
    "add_to_wishlist": 5.0, "cta_click": 6.0,
    "conversion": 10.0,     # ▲A8
    "rec_click": 4.0,       # ▲A8 positive feedback
    "rec_dismiss": -3.0,    # ▲A8 negative feedback
}
DWELL_BONUS = 2.0           # dwell > 30s
LAMBDA_LONG = math.log(2) / 72.0    # hours
LAMBDA_SHORT = math.log(2) / 6.0


def _score(events, lam, now):
    scores: dict[str, float] = defaultdict(float)
    for e in events:
        cat = e.get("category")
        if not cat:
            continue
        age_h = max(0.0, (now - e["ts"]).total_seconds() / 3600.0)
        w = WEIGHTS.get(e["event_type"], 0.0)
        if e["event_type"] == "product_dwell" and (e.get("dwell_ms") or 0) > 30_000:
            w += DWELL_BONUS
        if e["event_type"] == "scroll_depth" and (e.get("meta") or {}).get("depth", 0) >= 75:
            w += WEIGHTS["scroll_depth_75"]
        scores[cat] += w * math.exp(-lam * age_h)
    total = sum(v for v in scores.values() if v > 0) or 1.0
    return {k: max(0.0, v) / total for k, v in
            sorted(scores.items(), key=lambda kv: -kv[1])[:5]}


def score_interests(events, now=None):
    """events: [{event_type, category, ts, dwell_ms?, meta?}] → dict of vectors."""
    now = now or datetime.now(timezone.utc)
    long_v = _score(events, LAMBDA_LONG, now)
    short_v = _score(events, LAMBDA_SHORT, now)
    cats = set(long_v) | set(short_v)
    merged = {c: 0.6 * long_v.get(c, 0.0) + 0.4 * short_v.get(c, 0.0) for c in cats}
    tot = sum(merged.values()) or 1.0
    merged = {k: v / tot for k, v in sorted(merged.items(), key=lambda kv: -kv[1])[:5]}
    return {"long": long_v, "short": short_v, "merged": merged}


def fingerprint(merged: dict, price_band: str = "", stage: str = "") -> str:
    """Coarse on purpose: top-3 merged categories at 0.1 buckets + band + stage."""
    top3 = sorted(merged.items(), key=lambda kv: -kv[1])[:3]
    payload = "|".join(f"{c}:{round(s, 1)}" for c, s in top3) + f"|{price_band}|{stage}"
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def cosine_distance(a: dict, b: dict) -> float:
    cats = set(a) | set(b)
    if not cats:
        return 0.0
    va = [a.get(c, 0.0) for c in cats]
    vb = [b.get(c, 0.0) for c in cats]
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va)) or 1.0
    nb = math.sqrt(sum(x * x for x in vb)) or 1.0
    return 1.0 - dot / (na * nb)
