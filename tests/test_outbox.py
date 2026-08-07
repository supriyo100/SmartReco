"""Dual-write: products → vector_outbox → Chroma.

These tests never call Mesh. Embeddings are faked, deliberately: what needs
proving is the SYNC MECHANISM — that a write is queued, drained exactly once,
skipped when unchanged, and retried sanely when it fails. Whether the vector
itself is a good embedding is Mesh's job, not this module's, and a test that
depends on an external paid API is a test that fails for reasons unrelated to
the code it covers.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import delete, select

import app.catalog.outbox as outbox_mod
import app.catalog.vectors as vectors_mod
from app.catalog.outbox import _is_fatal, drain_all, drain_once
from app.db.models import Product, VectorOutbox
from app.db.session import async_session

DIM = 16


def fake_vectors(texts):
    out = []
    for t in texts:
        v = [0.0] * DIM
        for tok in t.lower().split():
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
        out.append(v)
    return out


class FakeCollection:
    """Stands in for Chroma. Records calls so the tests can assert on them."""

    def __init__(self):
        self.rows = {}
        self.upsert_calls = 0

    def upsert(self, ids, embeddings, metadatas, documents):
        self.upsert_calls += 1
        for i, _id in enumerate(ids):
            self.rows[_id] = {"embedding": embeddings[i], "metadata": metadatas[i],
                              "document": documents[i]}

    def delete(self, where=None, ids=None):
        if ids:
            for i in ids:
                self.rows.pop(i, None)
        if where and "parent_id" in where:
            for k in [k for k, v in self.rows.items()
                      if v["metadata"].get("parent_id") == where["parent_id"]]:
                del self.rows[k]

    def count(self):
        return len(self.rows)


class StatusError(Exception):
    def __init__(self, status):
        super().__init__(f"Error code: {status}")
        self.status_code = status


@pytest.fixture
def collection(monkeypatch):
    coll = FakeCollection()
    monkeypatch.setattr(vectors_mod, "get_collection", lambda: coll)

    # `is_query` is accepted because the real embed_batch takes it — nomic's
    # asymmetric prefixes — and a stub with a narrower signature would pass
    # here while the production call site raises TypeError.
    async def fake_embed(texts, *, is_query=False):
        return fake_vectors(texts)

    monkeypatch.setattr("app.agent.mesh.embed_batch", fake_embed)
    # Both gates are False under ENV=test, which would make drain a no-op.
    # `can_embed` is the one outbox reads now; `use_mesh` is kept patched for
    # any path that still asks specifically about Mesh.
    monkeypatch.setattr(outbox_mod.settings, "MESH_API_KEY", "test-key")
    monkeypatch.setattr(type(outbox_mod.settings), "use_mesh", property(lambda self: True))
    monkeypatch.setattr(type(outbox_mod.settings), "can_embed", property(lambda self: True))
    return coll


@pytest.fixture
async def product():
    """A throwaway product, removed afterwards along with its outbox rows."""
    slug = f"t-{uuid.uuid4().hex[:8]}"
    async with async_session() as s:
        p = Product(title="Test Course", slug=slug, description="A course about widgets.",
                    category="test", level="beginner", price=100.0, tags=["widget"],
                    prereq_ids=[], related_ids=[], instructor="T", rating=4.0)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    yield pid
    async with async_session() as s:
        await s.execute(delete(VectorOutbox).where(VectorOutbox.product_id == pid))
        await s.execute(delete(Product).where(Product.id == pid))
        await s.commit()


async def _enqueue(pid, op="upsert"):
    async with async_session() as s:
        s.add(VectorOutbox(product_id=pid, op=op))
        await s.commit()


async def _statuses(pid):
    async with async_session() as s:
        return [r[0] for r in (await s.execute(
            select(VectorOutbox.status).where(VectorOutbox.product_id == pid))).all()]


# --- the happy path ----------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_reaches_the_vector_store(collection, product):
    await _enqueue(product)
    report = await drain_once()
    assert report["upserted"] >= 1
    assert vectors_mod.chunk_id(product) in collection.rows
    assert await _statuses(product) == ["done"]


@pytest.mark.asyncio
async def test_embedded_document_contains_the_searchable_fields(collection, product):
    await _enqueue(product)
    await drain_once()
    doc = collection.rows[vectors_mod.chunk_id(product)]["document"]
    assert "Test Course" in doc and "widgets" in doc


@pytest.mark.asyncio
async def test_metadata_carries_filter_keys(collection, product):
    await _enqueue(product)
    await drain_once()
    meta = collection.rows[vectors_mod.chunk_id(product)]["metadata"]
    assert meta["parent_id"] == product
    assert meta["category"] == "test"
    assert meta["is_active"] is True
    assert None not in meta.values()          # Chroma rejects None


@pytest.mark.asyncio
async def test_unchanged_content_is_not_re_embedded(collection, product):
    await _enqueue(product)
    await drain_once()
    calls = collection.upsert_calls

    await _enqueue(product)                   # queued again, content identical
    report = await drain_once()
    assert report["upserted"] == 0
    assert collection.upsert_calls == calls, "spent an embedding call on unchanged content"


@pytest.mark.asyncio
async def test_edited_content_is_re_embedded(collection, product):
    await _enqueue(product)
    await drain_once()

    async with async_session() as s:
        p = (await s.execute(select(Product).where(Product.id == product))).scalar_one()
        p.description = "Rewritten: a course about submarines."
        await s.commit()
    await _enqueue(product)
    report = await drain_once()

    assert report["upserted"] == 1
    assert "submarines" in collection.rows[vectors_mod.chunk_id(product)]["document"]


@pytest.mark.asyncio
async def test_delete_removes_it_from_the_vector_store(collection, product):
    await _enqueue(product)
    await drain_once()
    assert vectors_mod.chunk_id(product) in collection.rows

    await _enqueue(product, op="delete")
    report = await drain_once()
    assert report["deleted"] == 1
    assert vectors_mod.chunk_id(product) not in collection.rows


@pytest.mark.asyncio
async def test_repeated_ops_collapse_to_one_embed(collection, product):
    """Five edits between drains are one upsert of the current state."""
    for _ in range(5):
        await _enqueue(product)
    await drain_once()
    assert collection.upsert_calls == 1


# --- failure handling --------------------------------------------------------

@pytest.mark.parametrize("status,fatal", [
    (400, True), (401, True), (402, True), (403, True), (404, True),
    (429, False), (500, False), (503, False),
])
def test_fatal_status_classification(status, fatal):
    assert _is_fatal(StatusError(status)) is fatal


@pytest.mark.asyncio
async def test_billing_error_halts_instead_of_retrying_per_item(collection, product, monkeypatch):
    """A 402 means every item fails the same way. Retrying each one turns a
    single refusal into N+1 charged calls."""
    calls = []

    async def boom(products):
        calls.append([p.id for p in products])
        raise StatusError(402)

    monkeypatch.setattr(vectors_mod, "upsert_products", boom)
    await _enqueue(product)
    report = await drain_all()

    assert len(calls) == 1, f"retried per item on an unretryable error: {calls}"
    assert "blocked" in report
    assert "balance" in report["blocked"].lower()
    assert await _statuses(product) == ["pending"], "work must remain owed"


@pytest.mark.asyncio
async def test_no_attempt_charged_for_unretryable_error(collection, product, monkeypatch):
    async def boom(products):
        raise StatusError(402)

    monkeypatch.setattr(vectors_mod, "upsert_products", boom)
    await _enqueue(product)
    await drain_all()
    async with async_session() as s:
        attempts = (await s.execute(select(VectorOutbox.attempts).where(
            VectorOutbox.product_id == product))).scalars().all()
    assert max(attempts) == 0


@pytest.mark.asyncio
async def test_transient_failure_falls_back_to_per_item_retry(collection, product, monkeypatch):
    """One poison row must not block everything queued behind it."""
    calls = []
    real = vectors_mod.upsert_products

    async def flaky(products):
        calls.append([p.id for p in products])
        if len(calls) == 1:
            raise StatusError(503)      # batch fails once, transiently
        return await real(products)

    monkeypatch.setattr(vectors_mod, "upsert_products", flaky)
    await _enqueue(product)
    report = await drain_once()

    assert len(calls) > 1, "did not retry after a transient failure"
    assert report["upserted"] == 1
    assert await _statuses(product) == ["done"]


@pytest.mark.asyncio
async def test_queued_product_that_no_longer_exists_is_not_retried_forever(collection):
    async with async_session() as s:
        s.add(VectorOutbox(product_id=999_999, op="upsert"))
        await s.commit()
    await drain_once()
    async with async_session() as s:
        rows = (await s.execute(select(VectorOutbox).where(
            VectorOutbox.product_id == 999_999))).scalars().all()
        assert all(r.status == "done" for r in rows)
        await s.execute(delete(VectorOutbox).where(VectorOutbox.product_id == 999_999))
        await s.commit()


@pytest.mark.asyncio
async def test_drain_is_a_noop_without_a_mesh_key(product, monkeypatch):
    monkeypatch.setattr(type(outbox_mod.settings), "use_mesh", property(lambda self: False))
    await _enqueue(product)
    report = await drain_once()
    assert "skipped" in report
    assert await _statuses(product) == ["pending"], "work must survive to be done later"
