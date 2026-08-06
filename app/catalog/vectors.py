"""The Chroma side of the dual-write.

One module owns writing to the vector store, so "what does a product look like
in Chroma" has exactly one answer. `ingest.py` writes rich multi-chunk
documents from the curated JSON; this writes rows for products that came from
the ADMIN FORM, which have no JSON file behind them and so cannot be chunked
the same way. Both land in the same collection with the same metadata keys, so
retrieval does not need to know which path produced a row.
"""
from __future__ import annotations

import hashlib

from app.config import settings

COLLECTION = "course_chunks"


def get_collection():
    """Chroma handle. Imported lazily — chromadb pulls in a large dependency
    tree, and the app must boot (and tests must run) without touching it."""
    import chromadb
    client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
    # NEVER the default embedding function (arch trap #1): vectors are always
    # supplied explicitly, so the store can't silently embed with a different
    # model than the one queries use.
    return client.get_or_create_collection(COLLECTION)


def product_document(p) -> str:
    """The text that represents an admin-authored product.

    Title and category are repeated into the embedded text on purpose: a
    description that never names its own subject retrieves poorly for the
    obvious query.
    """
    parts = [
        p.title,
        f"Category: {p.category}" if p.category else "",
        f"Level: {p.level}" if p.level else "",
        p.description or "",
        f"Topics: {', '.join(p.tags)}" if p.tags else "",
        f"Instructor: {p.instructor}" if p.instructor else "",
    ]
    return "\n".join(part for part in parts if part).strip()


def product_metadata(p) -> dict:
    """Chroma rejects None values in metadata, so every field is coerced.
    Keys match chunker.chunk_metadata so filters work across both paths."""
    return {
        "parent_id": p.id,
        "product_id": p.id,
        "slug": p.slug or "",
        "title": p.title or "",
        "category": p.category or "",
        "level": p.level or "",
        "price": float(p.price or 0.0),
        "rating": float(p.rating or 0.0),
        "is_active": bool(p.is_active),
        "chunk_type": "product",
        "source": "admin",
        "content_hash": content_hash(p),
    }


def content_hash(p) -> str:
    """Identifies the embedded content. If it is unchanged, re-embedding the
    product would spend an API call to produce a byte-identical vector."""
    return hashlib.sha256(product_document(p).encode("utf-8")).hexdigest()


def chunk_id(product_id: int) -> str:
    """Admin products occupy one deterministic id, so an upsert replaces the
    previous version rather than accumulating duplicates."""
    return f"product::{product_id}::0"


async def upsert_products(products: list) -> int:
    """Embed and upsert. Returns the number written."""
    if not products:
        return 0
    from app.agent.mesh import embed_batch

    texts = [product_document(p) for p in products]
    vectors = await embed_batch(texts)          # ▲A5 one call for the batch
    get_collection().upsert(
        ids=[chunk_id(p.id) for p in products],
        embeddings=vectors,
        metadatas=[product_metadata(p) for p in products],
        documents=texts,
    )
    return len(products)


def delete_product(product_id: int) -> None:
    """Remove every chunk for a product, whichever path created it.

    Deleting only chunk_id() would leave ingest-authored chunks retrievable for
    a course the admin just deactivated, so this deletes by metadata instead.
    """
    get_collection().delete(where={"parent_id": product_id})


def count() -> int:
    return get_collection().count()
