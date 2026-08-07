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


async def add_missing_columns(conn) -> list[str]:
    """Add columns present in the models but absent from an existing table.

    create_all() creates missing TABLES; it never alters one that already
    exists. So adding a field to a model and re-running init_db silently
    changes nothing, and the first query for that column fails at runtime with
    "no such column" — long after the command that should have caught it
    printed a success line.

    This is not a migration tool and does not pretend to be: it only ADDs
    columns. Renames, drops and type changes are not detected, and a NOT NULL
    column without a server default cannot be added to a populated table.
    Given a greenfield app with a four-day life that trade is deliberate
    (README §10) — but silently skipping the schema change was not.
    """
    added = []
    for table in Base.metadata.sorted_tables:
        rows = await conn.execute(text(f"PRAGMA table_info({table.name})"))
        existing = {r[1] for r in rows}
        if not existing:                     # table didn't exist; create_all made it
            continue
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(conn.dialect)}"
            default = getattr(col.default, "arg", None)
            if isinstance(default, (str, int, float, bool)):
                literal = f"'{default}'" if isinstance(default, str) else int(default) \
                    if isinstance(default, bool) else default
                ddl += f" DEFAULT {literal}"
            await conn.execute(text(ddl))
            added.append(f"{table.name}.{col.name}")
    return added


def _say(line: str) -> None:
    """Print a status line without assuming the console can encode it.

    A default Windows console is cp1252, which cannot encode U+2713 — so the
    original `print("✓ ...")` raised UnicodeEncodeError *after* the schema work
    had already committed. The command looked like it failed while the
    migration had in fact succeeded, which is the worst way for a setup script
    to behave. Falls back to ASCII rather than dropping the message.
    """
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


async def main():
    pathlib.Path("data").mkdir(exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        added = await add_missing_columns(conn)
        for ddl in FTS_DDL:
            await conn.execute(text(ddl))
    _say("✓ schema + FTS5 + triggers + indexes created")
    if added:
        _say(f"✓ added {len(added)} missing column(s) to existing tables:")
        for name in added:
            _say(f"    + {name}")


if __name__ == "__main__":
    asyncio.run(main())
