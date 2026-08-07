"""Embeddings, with a local fallback that cannot run out of balance.

Groq — the chat fallback — serves no embedding models, so when Mesh is down
there is no second API to call. The fallback has to be local, and that turns
out to be the better answer anyway: `nomic-ai/nomic-embed-text-v1` runs on CPU
through sentence-transformers, costs nothing per call, works with no network,
and is the one component of this system that cannot be disabled by a billing
event.

**The dimension trap.** Mesh's `text-embedding-3-small` is 1536-dim; nomic is
768. Vectors from two models are not comparable at all — cosine similarity
between them is noise, not a weaker signal. So a backend switch is not
transparent: the Chroma collection has to be rebuilt, and a query embedded with
one model must never be compared against documents embedded with the other.

Two things make that safe rather than a silent corruption:

  * `EMBED_BACKEND` defaults to "auto" but the *active* backend is recorded on
    every cached vector and reported by `backend_info()`, so a mismatch is
    visible rather than inferred from bad results.
  * `EmbeddingCache` is keyed on (backend, text) rather than text alone.
    Without that, a vector embedded by Mesh would be served to a nomic-indexed
    collection after a failover, which is the exact silent-corruption case.

The model loads lazily and once. First load pulls ~550 MB from HF Hub and takes
about a minute; every load after that is from the local cache and the encode
itself is ~50 ms for a batch.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

from sqlalchemy import select

from app.config import settings
from app.db.models import EmbeddingCache
from app.db.session import async_session

log = logging.getLogger("embeddings")

_model = None
_model_lock = asyncio.Lock()


def _cache_key(text: str, backend: str) -> str:
    """Cache key includes the backend.

    Keying on text alone was the bug waiting to happen: after a failover, a
    1536-dim Mesh vector would be returned for a collection built from 768-dim
    nomic vectors, and Chroma would either raise or — worse — accept it and
    return nonsense rankings.
    """
    return hashlib.sha256(f"{backend}\x00{text}".encode()).hexdigest()


async def _load_local():
    """Load the sentence-transformers model once, off the event loop.

    `SentenceTransformer(...)` does file IO and torch init, both blocking, so
    it runs in a thread — otherwise the first embedding request stalls every
    other request the server is handling.
    """
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:          # another task won the race
            return _model

        def _build():
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer(settings.LOCAL_EMBED_MODEL,
                                       trust_remote_code=True)

        log.info("loading local embedding model %s (first run downloads it)",
                 settings.LOCAL_EMBED_MODEL)
        _model = await asyncio.to_thread(_build)
        log.info("local embedding model ready")
    return _model


async def _embed_local(texts: list[str], is_query: bool) -> list[list[float]]:
    """Encode with nomic-embed.

    nomic is an ASYMMETRIC model: it expects `search_query:` on the thing being
    searched for and `search_document:` on the things being searched. Omitting
    the prefixes measurably degrades retrieval, and using the wrong one is
    worse than using neither — hence `is_query` is a required argument rather
    than a default anyone can forget.
    """
    model = await _load_local()
    prefix = "search_query: " if is_query else "search_document: "
    prepared = [prefix + t for t in texts]

    def _encode():
        return model.encode(prepared, normalize_embeddings=True,
                            show_progress_bar=False).tolist()

    return await asyncio.to_thread(_encode)


async def _embed_mesh(texts: list[str]) -> list[list[float]]:
    from app.agent.providers import is_provider_down, mark_down, mark_up, mesh_client

    try:
        resp = await mesh_client().embeddings.create(
            model=settings.EMBED_MODEL, input=texts)
    except Exception as exc:
        if is_provider_down(exc):
            mark_down("mesh", exc)
        raise
    mark_up("mesh")
    return [item.embedding for item in resp.data]


def active_backend() -> str:
    """Which backend the next call will use: "mesh" or "local"."""
    mode = (settings.EMBED_BACKEND or "auto").lower()
    if mode == "local":
        return "local"
    if mode == "mesh":
        return "mesh"

    from app.agent.providers import available

    if settings.MESH_API_KEY and settings.ENV != "test" and available("mesh"):
        return "mesh"
    return "local"


def backend_info() -> dict:
    """What is actually serving embeddings, for /admin and startup logs."""
    backend = active_backend()
    return {
        "backend": backend,
        "model": (settings.EMBED_MODEL if backend == "mesh"
                  else settings.LOCAL_EMBED_MODEL),
        "dim": None if backend == "mesh" else settings.LOCAL_EMBED_DIM,
        "mode": settings.EMBED_BACKEND,
    }


async def embed_batch(texts: list[str], *,
                      is_query: bool = False) -> list[list[float]]:
    """Embed a batch, cached, with automatic failover to the local model.

    `is_query` matters only for the local backend, where nomic's asymmetric
    prefixes apply. Mesh's model is symmetric and ignores it.
    """
    if not texts:
        return []

    backend = active_backend()
    keys = [_cache_key(t, backend) for t in texts]
    out: dict[str, list[float]] = {}

    async with async_session() as s:
        rows = (await s.execute(
            select(EmbeddingCache).where(EmbeddingCache.text_hash.in_(keys))
        )).scalars()
        for row in rows:
            out[row.text_hash] = row.vector

    missing = [(k, t) for k, t in zip(keys, texts) if k not in out]
    if missing:
        pending = [t for _, t in missing]
        if backend == "mesh":
            try:
                vectors = await _embed_mesh(pending)
            except Exception as exc:
                # A prompt cannot be malformed for an embedding call, so any
                # failure here is provider-level. Fall through to local rather
                # than failing the request — but re-key the cache, because
                # these vectors have a different dimension.
                if (settings.EMBED_BACKEND or "auto").lower() == "mesh":
                    raise
                log.warning("mesh embeddings failed (%s) — using %s",
                            type(exc).__name__, settings.LOCAL_EMBED_MODEL)
                backend = "local"
                keys = [_cache_key(t, backend) for t in texts]
                out = {}
                missing = list(zip(keys, texts))
                pending = list(texts)
                vectors = await _embed_local(pending, is_query)
        else:
            vectors = await _embed_local(pending, is_query)

        async with async_session() as s:
            for (key, _), vector in zip(missing, vectors):
                out[key] = vector
                s.add(EmbeddingCache(text_hash=key, vector=vector))
            await s.commit()

    return [out[k] for k in keys]
