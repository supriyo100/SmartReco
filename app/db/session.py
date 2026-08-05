from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA cache_size=-64000")   # 64 MB
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


async_session = async_sessionmaker(engine, expire_on_commit=False)


async def wal_checkpoint():
    """Nightly job (▲A16): checkpoint + truncate the -wal file."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
