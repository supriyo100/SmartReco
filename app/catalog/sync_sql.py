"""python -m app.catalog.sync_sql — load data/data_1/*.json into the products table.

The `products` row is the T1 filter surface (schema §0): what SQL filtering, FTS5
search and card rendering need. Chunking and embedding are ingest.py's job and
need a Mesh key; this does not, so the web app is browsable before any LLM
credentials exist.

Idempotent: keyed on slug, so re-running updates in place rather than duplicating.
Every write enqueues a vector_outbox row (§3.4) so a later ingest knows what to sync.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from sqlalchemy import select

from app.catalog.loader import as_product_row, load_all, validate_graph
from app.db.models import Product, VectorOutbox
from app.db.session import async_session

# Columns that live on the model. as_product_row also returns _mode (freshness
# input, §3.3), which has no column — it stays in the JSON document.
FIELDS = ("title", "slug", "description", "category", "level", "price", "tags",
          "prereq_ids", "related_ids", "instructor", "rating", "is_active")


def content_hash(row: dict) -> str:
    """What re-ingest compares to decide whether re-embedding is needed."""
    payload = json.dumps({k: row.get(k) for k in FIELDS}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def sync(dirpath: str = "data/data_1", allow_pending: bool = True) -> dict:
    courses = load_all(dirpath)
    pruned = validate_graph(courses, prune_pending=allow_pending)

    created = updated = unchanged = 0
    async with async_session() as s:
        for course in courses:
            row = as_product_row(course)
            row.pop("_mode", None)
            # Defaults for fields the curated document may legitimately omit.
            row["price"] = row.get("price") or 0.0
            row["rating"] = row.get("rating") or 0.0
            row["description"] = row.get("description") or ""
            digest = content_hash(row)

            product = (await s.execute(
                select(Product).where(Product.slug == row["slug"])
            )).scalar_one_or_none()

            if product is None:
                product = Product(**row, content_hash=digest)
                s.add(product)
                await s.flush()
                s.add(VectorOutbox(product_id=product.id, op="upsert"))
                created += 1
            elif product.content_hash != digest:
                for k, v in row.items():
                    setattr(product, k, v)
                product.content_hash = digest
                s.add(VectorOutbox(product_id=product.id, op="upsert"))
                updated += 1
            else:
                unchanged += 1
        await s.commit()

    return {"courses": len(courses), "created": created, "updated": updated,
            "unchanged": unchanged, "pruned_edges": len(pruned)}


if __name__ == "__main__":
    result = asyncio.run(sync())
    print(f"created {result['created']}, updated {result['updated']}, "
          f"unchanged {result['unchanged']} "
          f"({result['courses']} courses, {result['pruned_edges']} pending edges pruned)")
