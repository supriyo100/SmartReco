"""python -m app.catalog.catalogue — the curation tracker.

    data/data_1/*.json  →  one summary row per course  →  data/courses_catalogue.json

This is NOT data/catalog.index.json. That file is written by ingest and holds the
full course documents plus freshness, because the generate node needs the parent's
Tier-3 persuasion facts at answer time — it is a runtime lookup, it is large, and
it is keyed for random access by slug.

This file answers a different question: *what is in the catalog, and what is still
wrong with it?* One flat row per course — identity, the T1 filter fields, content
counts, chunk footprint, and a `flags` list of every open curation issue the tools
can detect mechanically. It is small enough to read in a diff, which is the point:
when a course is added or a cohort date passes, the change shows up here as a few
lines rather than buried in a 600-line document dump.

Flags are derived, never stored by hand. `validate_seed.py` prints them once and
they scroll away; this keeps them as state, so "3 courses still have a placeholder
source_url" is a fact you can look up instead of re-derive.

Usage:
  python -m app.catalog.catalogue                 # regenerate the tracker
  python -m app.catalog.catalogue --check         # fail if the file is out of date
  python -m app.catalog.catalogue path/to/courses

Note on --check: freshness is a function of today's date, so this file legitimately
drifts as time passes even when no course file changes. That is not noise — a
cohort quietly passing into the past is exactly the §3 defect this repo is most
afraid of, and --check turning red the morning after is the alarm working.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from collections import Counter
from datetime import date
from urllib.parse import urlparse

from app.catalog.chunker import build_chunks
from app.catalog.freshness import UNKNOWN_RECENCY, effective_mode, score_freshness
from app.catalog.loader import COURSE_DIR, load_all, strip_comments

CATALOGUE_PATH = "data/courses_catalogue.json"
CATALOGUE_VERSION = "1.0"

# Mirrors the skew test in ingest.report() on purpose — two places that judge
# "is this course hogging the index?" must not be allowed to disagree.
HOG_FAIR_SHARE_MULTIPLE = 3
HOG_MIN_SHARE = 0.15

# Thresholds validate_seed.py already warns on; repeated here so the tracker's
# picture of the catalog matches the validator's.
MAX_SKILLS = 30
MAX_MODULE_GROUPS = 12

# severity: "blocker" stops `make ingest` (or forces --allow-pending); "review" is
# a judgment call that should be confirmed and recorded in a _comment_, not silenced.
SEVERITY = {
    "past-cohort-start": "blocker",
    "pending-ladder-ref": "blocker",
    "source-url-placeholder": "blocker",
    "stale-cohort": "review",
    "no-recency-date": "review",
    "rating-null": "review",
    "no-module-groups": "review",
    "module-groups-over-max": "review",
    "skills-over-max": "review",
    "chunk-share-dominant": "review",
    "inactive": "review",
}


def _flag(code: str, detail: str) -> dict:
    return {"code": code, "severity": SEVERITY.get(code, "review"), "detail": detail}


def _is_placeholder_url(url: str | None) -> bool:
    """A bare domain root is not a verification target.

    §1 requires source_url to be somewhere a human can check every fact in the
    file. `https://academy.example.com/` satisfies the schema's `format: uri` and
    satisfies nothing else, so it is caught here rather than passing silently.
    """
    if not url:
        return True
    parts = urlparse(url)
    return not parts.netloc or (not parts.path.strip("/") and not parts.query)


def _course_flags(course: dict, slugs: set[str], fresh: dict, today: date) -> list[dict]:
    fmt = course.get("format") or {}
    flags: list[dict] = []

    # §3's worst defect: generate reads cohort_start as a live persuasion fact.
    start = fmt.get("cohort_start")
    if start and str(start)[:10] < today.isoformat():
        flags.append(_flag("past-cohort-start",
                           f"format.cohort_start {start} is in the past — move it to "
                           f"cohort_start_stale before this reaches generate (§3)"))

    for ref in course.get("prereq_ids", []) + course.get("related_ids", []):
        if ref not in slugs:
            flags.append(_flag("pending-ladder-ref",
                               f"'{ref}' is not a curated course — validate_graph() fails "
                               f"at ingest unless --allow-pending"))

    if _is_placeholder_url(course.get("source_url")):
        flags.append(_flag("source-url-placeholder",
                           f"source_url {course.get('source_url')!r} has no path — not a page "
                           f"a human can verify the file against (§1)"))

    # Declared live but demoted by the scorer: worth surfacing because the fix
    # (publish the next cohort date) restores a 0.55-weighted 1.00 prior.
    if fmt.get("mode") in ("live", "hybrid") and fresh["mode"] == "recorded":
        flags.append(_flag("stale-cohort",
                           f"declared {fmt['mode']} with no future cohort — scored at the "
                           f"recorded prior ({fresh['mode_prior']})"))

    if fresh["recency"] == UNKNOWN_RECENCY:
        flags.append(_flag("no-recency-date",
                           "no content_updated, published_at or cohort_start_stale — recency "
                           f"sits at the unknown floor ({UNKNOWN_RECENCY})"))

    if course.get("rating") is None:
        flags.append(_flag("rating-null", "rating is null — confirm it was absent at the "
                                          "source and not skipped"))

    groups = course.get("module_groups") or []
    if not groups:
        flags.append(_flag("no-module-groups",
                           "objectives-only course — valid, but it should never be a hero"))
    elif len(groups) > MAX_MODULE_GROUPS:
        flags.append(_flag("module-groups-over-max",
                           f"{len(groups)} groups > {MAX_MODULE_GROUPS} (§4)"))

    n_skills = len(course.get("skills") or [])
    if n_skills > MAX_SKILLS:
        flags.append(_flag("skills-over-max",
                           f"{n_skills} skills > {MAX_SKILLS} — trim the least discriminating"))

    if not course.get("is_active", True):
        flags.append(_flag("inactive", "is_active is false — excluded from retrieval"))

    return flags


def _counts(course: dict, n_chunks: int) -> dict:
    groups = course.get("module_groups") or []
    return {
        "objectives": len(course.get("objectives") or []),
        "objectives_dropped": len(course.get("objectives_dropped") or []),
        "module_groups": len(groups),
        "modules": sum(len(g.get("modules") or []) for g in groups),
        "projects": len(course.get("projects") or []),
        "skills": len(course.get("skills") or []),
        "mentors": len(course.get("mentors") or []),
        "perks": len(course.get("perks") or []),
        "chunks": n_chunks,
    }


def _comment_count(raw: dict) -> int:
    """How much audit trail this file carries. Not a quality score on its own, but
    a course with rich flags and no comments is one nobody has reasoned about."""
    n = sum(1 for k in raw if k.startswith("_comment"))
    for v in raw.values():
        if isinstance(v, dict):
            n += _comment_count(v)
    return n


def build_catalogue(dirpath: str = COURSE_DIR, today: date | None = None) -> dict:
    """Pure: reads the course files, returns the tracker document. No writes."""
    today = today or date.today()
    raws = load_all(dirpath, keep_comments=True)
    if not raws:
        raise SystemExit(f"no course files in {dirpath}/ — nothing to track")

    slugs = {r["slug"] for r in raws}
    paths = {r["slug"]: f"{dirpath}/{r['slug']}.json" for r in raws}

    rows: list[dict] = []
    for i, raw in enumerate(raws, start=1):
        course = strip_comments(raw)
        fresh = score_freshness(course, today)
        mode_for_scoring, mode_reason = effective_mode(course, today)
        n_chunks = len(list(build_chunks(course, product_id=i, today=today)))
        fmt = course.get("format") or {}

        rows.append({
            "slug": course["slug"],
            "title": course["title"],
            "file": paths[course["slug"]],
            "content_hash": "sha256:" + hashlib.sha256(
                pathlib.Path(paths[course["slug"]]).read_bytes()).hexdigest()[:16],

            "category": course["category"],
            "secondary_categories": course.get("secondary_categories", []),
            "level": course["level"],
            "level_range": course.get("level_range"),

            "price": course.get("price"),
            "price_band": course.get("price_band"),
            "currency": course.get("currency", "INR"),
            "is_free": course.get("is_free", False),
            "price_note": course.get("price_note"),
            "rating": course.get("rating"),

            "source_platform": course.get("source_platform", "own_site"),
            "source_url": course.get("source_url"),
            "is_active": course.get("is_active", True),

            "delivery": {
                "declared_mode": fmt.get("mode"),
                "effective_mode": mode_for_scoring,
                "mode_reason": mode_reason,
                "enrollment_status": fmt.get("enrollment_status", "open"),
                "duration": fmt.get("duration"),
                "duration_hours": fmt.get("duration_hours"),
                "cohort_start": fmt.get("cohort_start"),
                "cohort_start_stale": course.get("cohort_start_stale"),
                "content_updated": fmt.get("content_updated"),
                "published_at": fmt.get("published_at"),
                "curriculum_version": fmt.get("curriculum_version"),
            },
            "freshness": {
                "score": fresh["score"],
                "recency": fresh["recency"],
                "urgency": fresh["urgency"],
                "label": fresh["label"],
            },

            "counts": _counts(course, n_chunks),
            "ladder": {
                "prereq_ids": course.get("prereq_ids", []),
                "related_ids": course.get("related_ids", []),
            },
            "curation": {
                "comment_count": _comment_count(raw),
                "flags": _course_flags(course, slugs, fresh, today),
            },
        })

    _add_chunk_share(rows)
    for row in rows:
        f = row["curation"]["flags"]
        row["curation"]["status"] = (
            "blocked" if any(x["severity"] == "blocker" for x in f)
            else "needs_review" if f else "ready"
        )

    return {
        "catalogue_version": CATALOGUE_VERSION,
        "generated_at": today.isoformat(),
        "source_dir": dirpath,
        "_comment": "GENERATED by app/catalog/catalogue.py — do not hand-edit. Fix the "
                    "course file in source_dir and regenerate (`make catalogue`). This is "
                    "the curation tracker; data/catalog.index.json is the runtime Tier-3 "
                    "document lookup written by ingest, and the two are not interchangeable.",
        "course_count": len(rows),
        "totals": _totals(rows),
        "open_issues": _open_issues(rows),
        "courses": rows,
    }


def _add_chunk_share(rows: list[dict]) -> None:
    """Chunk-count skew is the failure mode chunking exists to prevent (§4): one
    course with 40 vectors dominates RRF and starves the other 59 out of the
    candidate set. Judged against fair share, not a fixed percentage — with 4
    courses curated somebody has to hold 25%."""
    total = sum(r["counts"]["chunks"] for r in rows) or 1
    fair = 1 / len(rows)
    for r in rows:
        share = r["counts"]["chunks"] / total
        r["counts"]["chunk_share"] = round(share, 4)
        if share > HOG_FAIR_SHARE_MULTIPLE * fair and share > HOG_MIN_SHARE:
            r["curation"]["flags"].append(_flag(
                "chunk-share-dominant",
                f"{share:.0%} of all chunks — {share / fair:.1f}x fair share; "
                f"merge module_groups (§4)"))


def _totals(rows: list[dict]) -> dict:
    def tally(key):
        return dict(sorted(Counter(k for k in (r[key] for r in rows) if k).items()))

    return {
        "by_category": tally("category"),
        "by_level": tally("level"),
        "by_price_band": tally("price_band"),
        "by_platform": tally("source_platform"),
        "by_declared_mode": dict(sorted(Counter(
            r["delivery"]["declared_mode"] for r in rows if r["delivery"]["declared_mode"]).items())),
        "by_effective_mode": dict(sorted(Counter(
            r["delivery"]["effective_mode"] for r in rows).items())),
        "by_enrollment_status": dict(sorted(Counter(
            r["delivery"]["enrollment_status"] for r in rows).items())),
        "active": sum(1 for r in rows if r["is_active"]),
        "free": sum(1 for r in rows if r["is_free"]),
        "price_null": sum(1 for r in rows if r["price"] is None),
        "rating_known": sum(1 for r in rows if r["rating"] is not None),
        "chunks": sum(r["counts"]["chunks"] for r in rows),
        "mean_freshness": round(
            sum(r["freshness"]["score"] for r in rows) / len(rows), 4),
    }


def _open_issues(rows: list[dict]) -> dict:
    """Catalog-level rollup — the reason this file exists rather than a per-course
    warning stream you have to re-read every time."""
    by_code: Counter = Counter()
    blockers: list[dict] = []
    for r in rows:
        for f in r["curation"]["flags"]:
            by_code[f["code"]] += 1
            if f["severity"] == "blocker":
                blockers.append({"slug": r["slug"], **f})

    pending = sorted({f["detail"].split("'")[1] for f in blockers
                      if f["code"] == "pending-ladder-ref"})
    return {
        "by_code": dict(sorted(by_code.items())),
        "blockers": blockers,
        "blocked_courses": sorted({b["slug"] for b in blockers}),
        "pending_slugs": pending,
        "_comment_pending_slugs": "Courses referenced by a ladder edge but not yet curated. "
                                  "This is the to-curate queue, in dependency order.",
        "ingest_ready": not blockers,
    }


def write(doc: dict, path: str = CATALOGUE_PATH) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def report(doc: dict) -> None:
    t = doc["totals"]
    print(f"\n{doc['course_count']} courses · {t['chunks']} chunks · "
          f"mean freshness {t['mean_freshness']:.3f}")
    print("  categories:", t["by_category"])
    print("  modes (declared → effective):", t["by_declared_mode"], "→", t["by_effective_mode"])

    print()
    for r in sorted(doc["courses"], key=lambda r: -r["freshness"]["score"]):
        mark = {"blocked": "✗", "needs_review": "⚠", "ready": "✓"}[r["curation"]["status"]]
        print(f"  {mark} {r['freshness']['score']:.3f}  {r['slug']:<44} "
              f"{r['counts']['chunks']:>3} chunks  {r['freshness']['label']}")
        for f in r["curation"]["flags"]:
            print(f"         [{f['severity']}] {f['code']}: {f['detail']}")

    issues = doc["open_issues"]
    if issues["pending_slugs"]:
        print("\n  to-curate queue (referenced by a ladder edge, no file yet):")
        for slug in issues["pending_slugs"]:
            print(f"    · {slug}")
    print(f"\n  ingest_ready: {issues['ingest_ready']}"
          f"{'' if issues['ingest_ready'] else '  (use --allow-pending, or clear the blockers)'}")


def main(dirpath: str = COURSE_DIR, check: bool = False) -> int:
    doc = build_catalogue(dirpath)
    report(doc)

    if check:
        existing = pathlib.Path(CATALOGUE_PATH)
        if not existing.exists():
            print(f"\n✗ {CATALOGUE_PATH} does not exist — run `make catalogue`")
            return 1
        # generated_at is excluded: the date alone changing is not drift worth
        # failing on, but anything it *causes* (a cohort passing, a freshness
        # score decaying) shows up in the rest of the document and does fail.
        old = {k: v for k, v in json.loads(existing.read_text(encoding="utf-8")).items()
               if k != "generated_at"}
        new = {k: v for k, v in doc.items() if k != "generated_at"}
        if old != new:
            print(f"\n✗ {CATALOGUE_PATH} is out of date — run `make catalogue`")
            return 1
        print(f"\n✓ {CATALOGUE_PATH} is up to date")
        return 0

    write(doc)
    print(f"\n✓ wrote {CATALOGUE_PATH} ({doc['course_count']} courses)")
    return 0


if __name__ == "__main__":
    _reconfigure = getattr(sys.stdout, "reconfigure", None)
    if _reconfigure:  # Windows cp1252 consoles choke on ✓/⚠/✗
        _reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(main(args[0] if args else COURSE_DIR, check="--check" in sys.argv))
