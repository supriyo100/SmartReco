#!/usr/bin/env python3
"""validate_seed.py — run this after EVERY course you save. Catches curation
mistakes early (missing fields, broken ladder refs, embedding-unsafe fields,
stale dates) before ingest burns Mesh calls on bad data.

Reads data/data_1/*.json — ONE FILE PER COURSE (manual curation workflow:
finish a course, save it, move to the next; no giant array to corrupt).

Structure is defined by data/course.schema.json; the rules a JSON Schema cannot
express are here. Every check below corresponds to a section of
data/COURSE_SCHEMA.md, cited in the message so a failure points at its rule.

Usage: python data/data_1/validate_seed.py [dir]
       python data/data_1/validate_seed.py --strict   # warnings also fail
"""
import glob
import json
import pathlib
import sys
from datetime import datetime

# Windows consoles default to cp1252 and die on the ✓/⚠/✗ markers and on ₹ in
# the price messages. Curation happens on Windows here, so this is not optional.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if _reconfigure:
    _reconfigure(encoding="utf-8", errors="replace")

REQUIRED = ["slug", "title", "overview", "category", "level", "is_free",
            "skills", "objectives", "source_url"]
NEVER_EMBED_KEYS = {"price", "mentors", "perks", "format", "currency",
                    "cohort_start_stale", "career_roles"}
LEVELS = {"beginner", "intermediate", "advanced"}
CATEGORIES = {"Agentic AI", "GenAI / LLM Engineering", "Data Science",
              "Data Engineering", "MLOps / LLMOps", "NLP", "Computer Vision",
              "Analytics", "Cloud"}
MODES = {"live", "hybrid", "self-paced", "recorded"}
ENROLLMENT = {"open", "closing_soon", "waitlist", "closed"}
BANDS = {"free": (0, 0), "low": (1, 2999), "mid": (3000, 9999), "high": (10000, 10**9)}


SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "course.schema.json"


def check_schema(loaded, errors):
    """Structural conformance to data/course.schema.json.

    Kept separate from the curation rules below, and non-fatal when jsonschema
    isn't installed: it arrives transitively here, and a curator should never be
    blocked from validating content because a dependency moved. The two
    artifacts checking the same files is the point — it is what stops the doc,
    the schema, and the validator from drifting apart.
    """
    try:
        import jsonschema
    except ImportError:
        print("· jsonschema not installed — structural checks skipped "
              "(pip install jsonschema); curation rules still enforced\n")
        return
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for path, p in loaded:
        tag = p.get("slug", path)
        for err in sorted(validator.iter_errors(p), key=lambda e: list(e.path)):
            loc = "/".join(str(x) for x in err.path) or "<root>"
            errors.append(f"[{tag}] schema: {loc}: {err.message}")


def load_courses(dirpath: str):
    """Returns [(filename, product_dict), ...]. Filename mismatch vs slug
    is itself a common manual-curation slip, so it's checked below."""
    out = []
    for path in sorted(glob.glob(f"{dirpath}/*.json")):
        try:
            out.append((path, json.loads(open(path, encoding="utf-8").read())))
        except json.JSONDecodeError as e:
            print(f"✗ {path}: INVALID JSON — {e}")
            sys.exit(1)
    return out


def _date(value):
    try:
        return datetime.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def check_pricing(p, tag, errors, warnings):
    """Schema §2. The null-price path exists because Udemy coupon pricing moves
    weekly; the band table exists because the first two curated files disagreed
    about what 'low' meant."""
    price = p.get("price")
    if price is None and not p.get("price_note"):
        errors.append(f"[{tag}] §2 price is null but no price_note explaining why "
                      f"(e.g. coupon-driven external pricing) — generate would have "
                      f"nothing to say about cost")
    elif price is not None and not isinstance(price, (int, float)):
        errors.append(f"[{tag}] §2 price must be numeric or explicitly null (with price_note)")

    band = p.get("price_band")
    if not p.get("is_free") and not band:
        errors.append(f"[{tag}] §2 paid course with no price_band")
    if band and band not in BANDS:
        errors.append(f"[{tag}] §2 price_band must be one of {sorted(BANDS)}, got {band!r}")
    elif band and isinstance(price, (int, float)):
        lo, hi = BANDS[band]
        if not lo <= price <= hi:
            errors.append(f"[{tag}] §2 price ₹{price:,.0f} contradicts price_band "
                          f"'{band}' (₹{lo:,}–₹{hi:,}) — bands are defined, not vibes")
    if p.get("is_free") and price not in (0, None):
        errors.append(f"[{tag}] §2 is_free is true but price is {price}")


def check_freshness(p, tag, errors, warnings, now):
    """Schema §3/§6 — the live-vs-recorded and recency inputs. A past date in
    format.cohort_start is the highest-severity defect this validator looks for:
    generate reads that field as a live persuasion fact."""
    fmt = p.get("format") or {}
    if not fmt:
        warnings.append(f"[{tag}] §3 no format block — freshness falls back to the "
                        f"unknown-recency floor (0.35) and a self-paced mode prior")
        return

    mode = fmt.get("mode")
    if mode not in MODES:
        errors.append(f"[{tag}] §3 format.mode must be one of {sorted(MODES)}, got {mode!r}")

    start = _date(fmt.get("cohort_start"))
    if fmt.get("cohort_start") and not start:
        errors.append(f"[{tag}] §3 format.cohort_start {fmt['cohort_start']!r} is not an ISO date")
    if start and start < now:
        errors.append(f"[{tag}] §3 format.cohort_start {fmt['cohort_start']} is in the PAST. "
                      f"Move it to top-level cohort_start_stale — generate reads cohort_start "
                      f"as a live fact and would tell users to enrol for a finished cohort")

    stale = _date(p.get("cohort_start_stale"))
    if stale and stale >= now:
        errors.append(f"[{tag}] §3 cohort_start_stale {p['cohort_start_stale']} is in the FUTURE "
                      f"— an upcoming cohort belongs in format.cohort_start where it can be "
                      f"scored and shown")

    status = fmt.get("enrollment_status")
    if status and status not in ENROLLMENT:
        errors.append(f"[{tag}] §3 enrollment_status must be one of "
                      f"{sorted(ENROLLMENT)}, got {status!r}")

    if mode in ("live", "hybrid") and not start:
        warnings.append(f"[{tag}] §6 declared '{mode}' with no future cohort — scored at the "
                        f"'recorded' prior (0.45), since what a buyer gets today is the replay. "
                        f"Add the next cohort date to restore the live prior")

    for field in ("content_updated", "published_at"):
        v = fmt.get(field)
        if v and not _date(v):
            errors.append(f"[{tag}] §3 format.{field} {v!r} is not an ISO date")
        elif (d := _date(v)) and d > now:
            errors.append(f"[{tag}] §3 format.{field} {v} is in the future")

    dated = fmt.get("content_updated") or fmt.get("published_at")
    if mode in ("self-paced", "recorded") and not dated:
        warnings.append(f"[{tag}] §6 '{mode}' with no content_updated or published_at — "
                        f"scored at the unknown floor (0.35), below the one-year mark. "
                        f"Correct if the date really isn't published; capture it if it is")


def check_content(p, tag, errors, warnings):
    """Schema §1/§4 — the retrieval surface."""
    if len(p.get("objectives", [])) < 2:
        warnings.append(f"[{tag}] §1 fewer than 2 objectives — weak retrieval surface")
    groups = p.get("module_groups", [])
    if not groups:
        warnings.append(f"[{tag}] §4 no module_groups — objectives-only course "
                        f"(fine for long tail, flag if this is a hero course)")
    if len(groups) > 12:
        warnings.append(f"[{tag}] §4 {len(groups)} module_groups — consider merging further; "
                        f">12 groups starts to dominate retrieval for a single course")
    for i, g in enumerate(groups):
        if not isinstance(g, dict) or "group" not in g or "modules" not in g:
            errors.append(f"[{tag}] §4 module_groups[{i}] must be {{group, modules[]}}")
    if len(p.get("skills", [])) > 30:
        warnings.append(f"[{tag}] §1 {len(p['skills'])} skills (>30) — confirm each is a real "
                        f"retrieval key; trim the least discriminating first")
    if p.get("rating") is None:
        warnings.append(f"[{tag}] §7 rating is null — confirmed not fabricated? "
                        f"(fine, just flagging)")

    # §7: null-with-a-comment is the standard; null in silence is an omission.
    comments = {k for k in p if k.startswith("_comment")}
    for field in ("rating", "price"):
        if p.get(field) is None and not any(field in c for c in comments):
            warnings.append(f"[{tag}] §7 {field} is null with no _comment_{field} recording why")


def main(dirpath: str, strict: bool = False):
    loaded = load_courses(dirpath)
    if not loaded:
        print(f"No .json files found in {dirpath}/ — nothing to validate yet.")
        return
    products = [p for _, p in loaded]
    slugs = {p.get("slug") for p in products}
    all_slugs = [p.get("slug") for p in products]
    errors, warnings = [], []
    now = datetime.now()
    check_schema(loaded, errors)

    for path, p in loaded:
        tag = p.get("slug", "<no slug>")
        expected_fname = f"{tag}.json"
        if path.replace("\\", "/").split("/")[-1] != expected_fname and tag != "<no slug>":
            errors.append(f"[{path}] §1 filename doesn't match slug '{tag}' — "
                          f"expected {expected_fname}. The loader refuses to ingest "
                          f"a mismatch, because one of the two is a typo")
        for f in REQUIRED:
            if f not in p or p[f] in (None, "", []):
                errors.append(f"[{tag}] §1 missing/empty required field: {f}")

        if p.get("category") not in CATEGORIES:
            errors.append(f"[{tag}] §1 category must be one of the canonical set "
                          f"{sorted(CATEGORIES)}, got {p.get('category')!r} — the B3 "
                          f"diversity cap counts this field, so a wrong bucket distorts "
                          f"the whole rec set")
        for sc in p.get("secondary_categories", []):
            if sc not in CATEGORIES:
                errors.append(f"[{tag}] §1 secondary_category {sc!r} not in the canonical set")

        if p.get("level") not in LEVELS:
            errors.append(f"[{tag}] §1 level must be one of {LEVELS} — this is the FUSION-MATCH "
                          f"anchor. For a course that genuinely spans levels (e.g. "
                          f"Python-to-advanced-agents), set level to its PRIMARY audience and use "
                          f"level_range:[min,max] alongside it, got level={p.get('level')!r}")
        lr = p.get("level_range")
        if lr and (len(lr) != 2 or any(x not in LEVELS for x in lr)):
            errors.append(f"[{tag}] §1 level_range must be [min, max] from {LEVELS}, got {lr!r}")

        check_pricing(p, tag, errors, warnings)
        check_freshness(p, tag, errors, warnings, now)
        check_content(p, tag, errors, warnings)

        for ref in p.get("prereq_ids", []) + p.get("related_ids", []):
            if ref not in slugs:
                warnings.append(f"[{tag}] §5 ladder ref '{ref}' does not exist among curated "
                                f"courses so far ({len(products)} loaded) — fine if you haven't "
                                f"written that course's file yet, just confirm the slug spelling "
                                f"later. Ingest WILL fail on it (validate_graph)")
            if ref == tag:
                errors.append(f"[{tag}] §5 course references itself in the ladder")

    dupe_slugs = [s for s in slugs if all_slugs.count(s) > 1]
    if dupe_slugs:
        errors.append(f"duplicate slugs across files: {dupe_slugs}")

    print(f"{len(products)} course file(s) in {dirpath}/\n")
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
    if warnings and strict:
        print("✗ --strict: warnings treated as errors")
        sys.exit(1)
    print("✓ all course files valid, all ladder refs resolve (or are pending future files).")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "data/data_1", "--strict" in sys.argv)
