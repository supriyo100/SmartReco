#!/usr/bin/env python3
"""validate_seed.py — run this BEFORE `make seed`. Catches curation mistakes
early (missing fields, broken ladder refs, embedding-unsafe fields, stale
dates) so you don't burn Mesh calls re-seeding after a typo.

Usage: python validate_seed.py seed/products.json
"""
import json
import sys
from datetime import datetime

REQUIRED = ["slug", "title", "overview", "category", "level", "price",
            "is_free", "skills", "objectives", "source_url"]
NEVER_EMBED_KEYS = {"price", "mentors", "perks", "format", "currency"}
LEVELS = {"beginner", "intermediate", "advanced"}


def main(path: str):
    products = json.loads(open(path).read())
    slugs = {p["slug"] for p in products}
    errors, warnings = [], []

    for p in products:
        tag = p.get("slug", "<no slug>")
        for f in REQUIRED:
            if f not in p or p[f] in (None, "", []):
                errors.append(f"[{tag}] missing/empty required field: {f}")
        if p.get("level") not in LEVELS:
            errors.append(f"[{tag}] level must be one of {LEVELS}, got {p.get('level')!r}")
        if not isinstance(p.get("price"), (int, float)):
            errors.append(f"[{tag}] price must be numeric")
        for ref in p.get("prereq_ids", []) + p.get("related_ids", []):
            if ref not in slugs:
                errors.append(f"[{tag}] ladder ref '{ref}' does not exist in this file")
        if len(p.get("objectives", [])) < 2:
            warnings.append(f"[{tag}] fewer than 2 objectives — weak retrieval surface")
        if not p.get("module_groups"):
            warnings.append(f"[{tag}] no module_groups — objectives-only course (fine for long tail, flag if this is a hero course)")
        for k in NEVER_EMBED_KEYS:
            if k in p.get("objectives", []) or any(k in o.lower() for o in p.get("objectives", [])):
                warnings.append(f"[{tag}] objective text mentions '{k}' — check it isn't leaking a persuasion fact into a retrieval chunk")
        stale = p.get("cohort_start_stale") or p.get("cohort_start")
        if stale:
            try:
                dt = datetime.fromisoformat(stale)
                if dt < datetime.now():
                    warnings.append(f"[{tag}] cohort start {stale} is in the past — confirm it's excluded from generate context")
            except ValueError:
                pass

    dupes = [s for s in slugs if [p["slug"] for p in products].count(s) > 1]
    if dupes:
        errors.append(f"duplicate slugs: {dupes}")

    print(f"{len(products)} products checked.\n")
    if warnings:
        print(f"⚠ {len(warnings)} warnings:")
        for w in warnings:
            print(" -", w)
        print()
    if errors:
        print(f"✗ {len(errors)} errors:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("✓ schema valid, all ladder refs resolve.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "seed/products.json")
