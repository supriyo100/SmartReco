import os

os.environ.setdefault("ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
# Mail must take the store-to-disk path under test. Set before app.config is
# imported, so no test run can reach a real SMTP server even if a developer
# has working credentials in their .env.
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASS"] = ""

import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def db():
    """Ensure the schema exists before a test that touches tables.

    Autouse, because the alternative is every DB-touching test file growing its
    own setup and the ones that forget failing with `no such table: users` —
    which is exactly how test_chat.py and test_profiles.py were failing. The
    cost is one cheap no-op `create_all` per test.

    Creates and does NOT drop. `create_all` is a no-op on tables that already
    exist, so this is safe to request from any test, and dropping would be
    actively harmful: the whole suite shares one DATABASE_URL, so a teardown
    that removed the schema would break every test that ran afterwards —
    including the ones that never asked for this fixture.

    Rows are not cleaned up either. Each test here creates its own users with
    distinct emails, and the assertions are scoped to the ids it just made, so
    leftover rows are inert.
    """
    from app.db.models import Base
    from app.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
