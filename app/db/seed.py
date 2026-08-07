"""python -m app.db.seed — load seed/products.json into SQL + Chroma.
Uses mesh.embed_batch (▲A5): 60 products ≈ 1-2 embedding API calls, not 60.
Day-1 simplification (arch v2 §8): synchronous dual-write here; outbox
upgrade lands Aug 8 AM if the core loop is green.
"""
import asyncio
import hashlib
import json
import pathlib

import chromadb

from app.agent.mesh import embed_batch
from app.config import settings
from app.db.models import Product
from app.db.session import async_session


def embedding_text(p: dict) -> str:
    return (f"{p['title']}. Category: {p['category']}. Level: {p['level']}. "
            f"Tags: {', '.join(p.get('tags', []))}. {p['description']}")


def validate_graph(products: list[dict]):
    """▲B6: every prereq/related slug must exist. Fail loudly, before any writes."""
    slugs = {p["slug"] for p in products}
    bad = [(p["slug"], ref) for p in products
           for ref in (p.get("prereq_ids", []) + p.get("related_ids", []))
           if ref not in slugs]
    if bad:
        raise SystemExit(f"seed graph references unknown slugs: {bad}")


async def main():
    products = json.loads(pathlib.Path("seed/products.json").read_text())
    validate_graph(products)
    texts = [embedding_text(p) for p in products]
    vectors = await embed_batch(texts) if settings.can_embed else None

    client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
    coll = client.get_or_create_collection("products")  # NEVER default embed fn (trap #1)

    async with async_session() as s:
        objs = []
        for p, t in zip(products, texts):
            obj = Product(**{k: v for k, v in p.items()}, content_hash=hashlib.sha256(t.encode()).hexdigest())
            s.add(obj)
            objs.append(obj)
        await s.commit()
        for o in objs:
            await s.refresh(o)

    if vectors:
        coll.upsert(
            ids=[str(o.id) for o in objs],
            embeddings=vectors,
            metadatas=[{"category": p["category"], "level": p["level"],
                        "price": p["price"], "rating": p.get("rating", 4.5),
                        "is_active": True, "content_hash": o.content_hash}
                       for p, o in zip(products, objs)],
            documents=texts,
        )
        print(f"✓ seeded {len(objs)} products → SQL + Chroma ({len(vectors)} vectors, batched)")
    else:
        print(f"✓ seeded {len(objs)} products → SQL only (no MESH_API_KEY — Chroma skipped)")


if __name__ == "__main__":
    asyncio.run(main())
