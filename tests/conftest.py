import os
import pathlib

os.environ.setdefault("ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

# The test database is a SEPARATE FILE, always. This must be set before
# app.config or app.db.session is imported anywhere.
#
# Learned the hard way: without it, DATABASE_URL points at ./data/smartreco.db
# — the real development database — and every fixture row a test creates is
# written into it permanently. A suite run left 36 "Test Course" products in
# the dev catalog, which then showed up on the browse page, in recommendations,
# and in digest emails. Tests must never be able to touch dev data.
_TEST_DB = pathlib.Path("data/test_smartreco.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./{_TEST_DB.as_posix()}"

# Mail must take the store-to-disk path under test, so no test run can reach a
# real SMTP server even with working credentials in a developer's .env.
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASS"] = ""

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


def pytest_sessionstart(session):
    """Start each run from an empty test database.

    Deleting the file up front rather than dropping tables in a fixture keeps
    the guarantee simple: whatever a previous run left behind is gone before
    the first test, and a developer inspecting failures afterwards still has
    the final state to look at.
    """
    for suffix in ("", "-wal", "-shm"):
        pathlib.Path(str(_TEST_DB) + suffix).unlink(missing_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _guard_database_url():
    """Fail loudly if anything re-pointed the engine at the real database."""
    from app.db.session import engine

    url = str(engine.url)
    assert "test_smartreco" in url, (
        f"tests are pointed at {url!r} — refusing to run against a non-test "
        "database. Check that conftest.py is imported before app.config."
    )
    yield


@pytest_asyncio.fixture(autouse=True)
async def db():
    """Ensure the schema exists before a test that touches tables.

    Autouse, because the alternative is every DB-touching test file growing its
    own setup and the ones that forget failing with `no such table: users` —
    which is exactly how test_chat.py and test_profiles.py were failing. The
    cost is one cheap no-op `create_all` per test.

    Rows are not cleaned up between tests. Each test creates its own users and
    products with distinct slugs and asserts against the ids it just made, so
    leftovers are inert — and the whole file is deleted at session start.
    """
    from app.db.models import Base
    from app.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
