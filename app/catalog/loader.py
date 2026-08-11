"""app/catalog/loader.py — read data/data_1/*.json (one file per course, the
manual curation workflow) into memory, strip curation notes, and enforce the
ladder invariant before anything is written or embedded.

Structural validation belongs to data/course.schema.json; curation-rule
validation belongs to validate_seed.py. This module does the third job: the
checks that must run at *ingest* time regardless of whether anyone remembered
to run the validator.
"""
from __future__ import annotations

import glob
import json
import pathlib

COURSE_DIR = "data/data_1"


def strip_comments(course: dict) -> dict:
    """Drop `_comment_*` keys, recursively.

    They are the audit trail (schema §7) and must survive in the files forever,
    but they must never reach an embedding — a paragraph explaining why a price
    is null would otherwise become part of the course's semantic identity.
    """
    out = {}
    for k, v in course.items():
        if k.startswith("_comment"):
            continue
        out[k] = strip_comments(v) if isinstance(v, dict) else v
    return out


def load_all(dirpath: str = COURSE_DIR, keep_comments: bool = False) -> list[dict]:
    courses = []
    for path in sorted(glob.glob(f"{dirpath}/*.json")):
        raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        stem = pathlib.Path(path).stem
        if raw.get("slug") != stem:
            raise SystemExit(
                f"{path}: slug {raw.get('slug')!r} != filename stem {stem!r}. "
                f"The slug is the identity other courses' ladder edges point at; "
                f"a mismatch here means one of the two is a typo (schema §1)."
            )
        courses.append(raw if keep_comments else strip_comments(raw))
    return courses


def load_one(slug: str, dirpath: str = COURSE_DIR) -> dict | None:
    """Single-course read by slug, for request-time lookups (the enroll page,
    subscribed-courses) that don't need the whole catalog scanned. None if the
    slug has no curated file — callers fall back to Product-only data."""
    path = pathlib.Path(dirpath) / f"{slug}.json"
    if not path.is_file():
        return None
    return strip_comments(json.loads(path.read_text(encoding="utf-8")))


def validate_graph(courses: list[dict], prune_pending: bool = False) -> list[tuple[str, str]]:
    """▲B6: every prereq/related slug must exist. Fail loudly, before any writes.

    Forward references are fine while curating (validate_seed.py reports them as
    pending), but by ingest time they are dangling edges that would render a
    'Next step' card pointing at nothing.

    `prune_pending` is the incremental-curation escape hatch: with 3 of ~60
    courses written, every ladder edge points at a file that doesn't exist yet,
    and refusing to ingest would mean no RAG index until the catalog is complete.
    It drops the unresolvable edges IN MEMORY ONLY — the files keep them, so the
    edges reconnect by themselves as the missing courses land. Returns what it
    pruned so the caller can report it rather than swallow it.
    """
    slugs = {c["slug"] for c in courses}
    bad = [(c["slug"], ref) for c in courses
           for ref in (c.get("prereq_ids", []) + c.get("related_ids", []))
           if ref not in slugs]
    if not bad:
        return []
    if not prune_pending:
        raise SystemExit(
            f"catalog graph references unknown slugs: {bad}\n"
            f"  → fix the spelling, curate the missing course, or re-run with "
            f"--allow-pending to ingest with these edges dropped in memory."
        )
    for c in courses:
        for field in ("prereq_ids", "related_ids"):
            if c.get(field):
                c[field] = [r for r in c[field] if r in slugs]
    return bad


def as_product_row(course: dict) -> dict:
    """Project the rich course document onto the flat `products` SQL columns.

    The SQL row is the T1 filter surface (schema §0) plus enough to render a
    card; the full document stays with the chunks for T2/T3. Deliberately lossy.
    """
    fmt = course.get("format") or {}
    mentors = course.get("mentors") or []
    return {
        "title": course["title"],
        "slug": course["slug"],
        "description": course["overview"],
        "category": course["category"],
        "level": course["level"],
        "price": course.get("price"),
        "tags": course.get("skills", []),
        "prereq_ids": course.get("prereq_ids", []),
        "related_ids": course.get("related_ids", []),
        "instructor": mentors[0] if mentors else "",
        "rating": course.get("rating"),
        "is_active": course.get("is_active", True),
        "_mode": fmt.get("mode"),
    }
