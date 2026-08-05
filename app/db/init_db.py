"""python -m app.db.init_db — create schema, FTS5, triggers, indexes."""
import asyncio
import pathlib

from sqlalchemy import text

from app.db.models import Base
from app.db.session import engine

FTS_DDL = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
         title, description, category, tags,
         content=products, content_rowid=id)""",
    """CREATE TRIGGER IF NOT EXISTS products_fts_ai AFTER INSERT ON products BEGIN
         INSERT INTO products_fts(rowid, title, description, category, tags)
         VALUES (new.id, new.title, new.description, new.category, new.tags);
       END""",
    """CREATE TRIGGER IF NOT EXISTS products_fts_ad AFTER DELETE ON products BEGIN
         INSERT INTO products_fts(products_fts, rowid, title, description, category, tags)
         VALUES ('delete', old.id, old.title, old.description, old.category, old.tags);
       END""",
    """CREATE TRIGGER IF NOT EXISTS products_fts_au AFTER UPDATE ON products BEGIN
         INSERT INTO products_fts(products_fts, rowid, title, description, category, tags)
         VALUES ('delete', old.id, old.title, old.description, old.category, old.tags);
         INSERT INTO products_fts(rowid, title, description, category, tags)
         VALUES (new.id, new.title, new.description, new.category, new.tags);
       END""",
    # ▲A6 outbox indexes
    "CREATE INDEX IF NOT EXISTS idx_outbox_status_created ON vector_outbox(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_attempts ON vector_outbox(attempts) WHERE status='pending'",
]


async def main():
    pathlib.Path("data").mkdir(exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in FTS_DDL:
            await conn.execute(text(ddl))
    print("✓ schema + FTS5 + triggers + indexes created")


if __name__ == "__main__":
    asyncio.run(main())
