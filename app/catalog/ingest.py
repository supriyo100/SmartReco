"""python -m app.catalog.ingest — the RAG ingest path.

    data/data_1/*.json  →  validate  →  chunk  →  embed (batched)  →  Chroma
                                          └─────→  data/catalog.index.json

Chunk-level ingest, not document-level: a 12-month course is not one idea, and
embedding it as one vector means every query matches it weakly and nothing
matches it well. Chunking is in chunker.py; this module is the pipeline around it.

Embeddings go through mesh.embed_batch (▲A5), so the whole catalog costs 1-2 API
calls rather than one per chunk. Chroma's default embedding function is never
used (trap #1) — vectors are always supplied explicitly.

Usage:
  python -m app.catalog.ingest                  # ingest data/data_1
  python -m app.catalog.ingest --dry-run        # chunk + report, zero API calls
  python -m app.catalog.ingest --allow-pending  # tolerate ladder edges to
                                                # un-curated courses (see loader)
  python -m app.catalog.ingest path/to/courses
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import sys
from collections import Counter
from datetime import date

from app.catalog.chunker import build_chunks
from app.catalog.freshness import score_freshness
from app.catalog.loader import COURSE_DIR, load_all, validate_graph
from app.config import settings

COLLECTION = "course_chunks"
INDEX_PATH = "data/catalog.index.json"


def build_catalog(dirpath: str, today: date | None = None,
                  allow_pending: bool = False) -> tuple[list[dict], list[dict]]:
    """Returns (courses, chunks). Pure — no I/O beyond reading the JSON files,
    so --dry-run and the real run share exactly one code path."""
    courses = load_all(dirpath)
    if not courses:
        raise SystemExit(f"no course files in {dirpath}/ — nothing to ingest")
    pruned = validate_graph(courses, prune_pending=allow_pending)
    if pruned:
        print(f"⚠ --allow-pending dropped {len(pruned)} ladder edge(s) pointing at "
              f"un-curated courses (files unchanged; they reconnect on the next "
              f"ingest once those courses exist):")
        for slug, ref in pruned:
            print(f"    {slug} → {ref}")

    chunks: list[dict] = []
    for i, course in enumerate(courses, start=1):
        # Stable synthetic id: ingest runs before SQL rows exist, and the slug is
        # the real identity anyway (loader enforces slug == filename).
        chunks.extend(build_chunks(course, product_id=i, today=today))
    return courses, chunks


def write_index(courses: list[dict], today: date | None = None) -> None:
    """The full documents, keyed by slug, for Tier-3 generate-time injection.

    Retrieval returns chunks; the generate node needs the parent's persuasion
    facts (price, format, perks, mentors) which are deliberately not embedded.
    This file is that lookup — resolved once at ingest so the hot path never
    re-reads and re-parses 60 JSON files.
    """
    index = {
        c["slug"]: {**c, "freshness": score_freshness(c, today)}
        for c in courses
    }
    path = pathlib.Path(INDEX_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def report(courses: list[dict], chunks: list[dict], today: date | None = None) -> None:
    by_type = Counter(c["metadata"]["chunk_type"] for c in chunks)
    print(f"\n{len(courses)} courses → {len(chunks)} chunks")
    print("  by type:", dict(by_type))

    # Chunk-count skew is the failure mode chunking exists to prevent, so it is
    # reported every run rather than left for someone to notice in retrieval.
    per_course = Counter(c["metadata"]["slug"] for c in chunks)
    hog, n = per_course.most_common(1)[0]
    share = n / len(chunks)
    fair_share = 1 / len(courses)
    print(f"  largest course: {hog} ({n} chunks, {share:.0%} of catalog; "
          f"fair share {fair_share:.0%})")
    # Judged against fair share, not a fixed percentage: with 3 courses curated
    # someone must hold 33%, and a flat 40% threshold would cry wolf on every
    # early run until the catalog fills out.
    if share > 3 * fair_share and share > 0.15:
        print(f"  ⚠ {hog} holds {share:.0%} of all chunks — {share / fair_share:.1f}x its "
              f"fair share, enough to dominate RRF. Merge its module_groups (schema §4).")

    print("\n  freshness (live-vs-recorded + recency, schema §6):")
    for c in sorted(courses, key=lambda c: -score_freshness(c, today)["score"]):
        f = score_freshness(c, today)
        print(f"    {f['score']:.3f}  {c['slug']:<48} {f['label']}")
        for line in f["explain"]:
            print(f"           · {line}")


async def main(dirpath: str = COURSE_DIR, dry_run: bool = False,
               allow_pending: bool = False) -> None:
    courses, chunks = build_catalog(dirpath, allow_pending=allow_pending)
    report(courses, chunks)
    write_index(courses)
    print(f"\n✓ wrote {INDEX_PATH} ({len(courses)} courses, full docs + freshness)")

    if dry_run:
        print("✓ dry run — no embeddings requested, Chroma untouched")
        return
    if not settings.use_mesh:
        print("✓ no MESH_API_KEY (or ENV=test) — index written, Chroma skipped")
        return

    # Imported here, not at module scope: mesh.py builds its OpenAI client on
    # import and raises without a key, which would make --dry-run — the mode
    # whose whole point is needing no credentials — impossible to run.
    import chromadb

    from app.agent.mesh import embed_batch

    texts = [c["text"] for c in chunks]
    vectors = await embed_batch(texts)

    client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
    coll = client.get_or_create_collection(COLLECTION)  # NEVER default embed fn (trap #1)
    coll.upsert(
        ids=[c["id"] for c in chunks],
        embeddings=vectors,
        metadatas=[{**c["metadata"],
                    "content_hash": hashlib.sha256(c["text"].encode()).hexdigest()}
                   for c in chunks],
        documents=texts,
    )
    print(f"✓ upserted {len(chunks)} chunks → Chroma '{COLLECTION}' "
          f"({len(vectors)} vectors, batched)")


if __name__ == "__main__":
    _reconfigure = getattr(sys.stdout, "reconfigure", None)
    if _reconfigure:  # Windows cp1252 consoles choke on ✓/⚠/₹
        _reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(args[0] if args else COURSE_DIR,
                     dry_run="--dry-run" in sys.argv,
                     allow_pending="--allow-pending" in sys.argv))
