"""Smoke tests — /healthz and non-blocking event ingest. Run: make test.
ENV=test in conftest keeps Mesh out of the loop (settings.use_mesh False)."""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_healthz():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.asyncio
async def test_event_ingest_202():
    payload = {"events": [{
        "event_uuid": str(uuid.uuid4()), "event_type": "product_view",
        "product_id": 1, "meta": {}, "ts": "2026-08-05T12:00:00Z"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/events", json=payload)
    assert r.status_code == 202 and r.json()["accepted"] == 1
