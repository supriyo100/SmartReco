"""Single gateway for all Mesh API calls: chat, structured chat, embeddings.

▲A2  structured_call: MODEL_FAST first; on parse failure retry once with
     MODEL_FAST_FALLBACK (gpt-4o-mini — reliable json_schema). The v1 trap
     list knew response_format fails silently; this is the runtime answer.
▲A5  embed_batch: one POST per batch, read-through EmbeddingCache.
"""
import asyncio
import hashlib
import json
import logging

from openai import AsyncOpenAI
from sqlalchemy import select

from app.config import settings
from app.db.models import EmbeddingCache
from app.db.session import async_session

log = logging.getLogger("mesh")

client = AsyncOpenAI(base_url=settings.MESH_BASE_URL, api_key=settings.MESH_API_KEY)

RETRY_STATUS = {429, 500, 502, 503}


def _parse_json(raw: str) -> dict:
    """▲B1 last line of defense: some models return prose-wrapped or
    fence-wrapped JSON even under response_format. Strip markdown fences,
    then extract the outermost {...} before parsing."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        txt = txt[4:] if txt.lower().startswith("json") else txt
        txt = txt.strip().rstrip("`").strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start == -1 or end == -1:
        raise json.JSONDecodeError("no JSON object found", raw, 0)
    return json.loads(txt[start:end + 1])


async def _chat(model: str, messages: list, **kw):
    delay = 1.0
    for attempt in range(4):
        try:
            return await client.chat.completions.create(model=model, messages=messages, **kw)
        except Exception as e:  # backoff on transient errors
            status = getattr(e, "status_code", None)
            if attempt == 3 or (status is not None and status not in RETRY_STATUS):
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def structured_call(messages: list, schema: dict, schema_name: str = "out") -> tuple[dict, str, bool]:
    """Returns (parsed, model_used, fallback_used)."""
    rf = {"type": "json_schema",
          "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
    try:
        resp = await _chat(settings.MODEL_FAST, messages, response_format=rf, temperature=0)
        return _parse_json(resp.choices[0].message.content), settings.MODEL_FAST, False
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as e:
        log.warning("structured parse failed on %s (%s) → fallback %s",
                    settings.MODEL_FAST, e, settings.MODEL_FAST_FALLBACK)
    resp = await _chat(settings.MODEL_FAST_FALLBACK, messages, response_format=rf, temperature=0)
    return _parse_json(resp.choices[0].message.content), settings.MODEL_FAST_FALLBACK, True


async def writer_call(messages: list, schema: dict) -> tuple[dict, str]:
    """Persuasive generation under a strict schema (arch v1 §5.4 generate)."""
    rf = {"type": "json_schema",
          "json_schema": {"name": "recommendation", "schema": schema, "strict": True}}
    resp = await _chat(settings.MODEL_WRITER, messages, response_format=rf, temperature=0.7)
    return _parse_json(resp.choices[0].message.content), settings.MODEL_WRITER


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """▲A5: one API call for the whole batch, with read-through cache."""
    hashes = [_h(t) for t in texts]
    out: dict[str, list[float]] = {}
    async with async_session() as s:
        rows = (await s.execute(
            select(EmbeddingCache).where(EmbeddingCache.text_hash.in_(hashes)))).scalars()
        for r in rows:
            out[r.text_hash] = r.vector
    missing = [(h, t) for h, t in zip(hashes, texts) if h not in out]
    if missing:
        resp = await client.embeddings.create(model=settings.EMBED_MODEL,
                                              input=[t for _, t in missing])
        async with async_session() as s:
            for (h, _), item in zip(missing, resp.data):
                out[h] = item.embedding
                s.add(EmbeddingCache(text_hash=h, vector=item.embedding))
            await s.commit()
    return [out[h] for h in hashes]


async def check_models_at_startup():
    """Log structured-output support per configured model. Don't assume."""
    try:
        models = await client.models.list()
        ids = {m.id for m in models.data}
        for m in (settings.MODEL_FAST, settings.MODEL_FAST_FALLBACK, settings.MODEL_WRITER):
            log.info("mesh model %s: %s", m, "available" if m in ids else "NOT LISTED — verify")
    except Exception as e:
        log.warning("could not list Mesh models at startup: %s", e)
