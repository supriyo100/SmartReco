"""Career advisor: grounding, safety and the offline path.

The load-bearing test here is `test_ungrounded_citations_are_stripped`. The
prompt tells the model it may only name retrieved courses, but a rule stated in
a prompt is a request — `_enforce_grounding` is what makes it a guarantee, and
it is the chat equivalent of the validate node (§6).
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.chat.agent import _enforce_grounding, _offline_reply
from app.chat.retrieval import fts_query
from app.main import app

# --- grounding ---------------------------------------------------------------

def test_ungrounded_citations_are_stripped():
    """A model naming a course we do not sell is a lie with our brand on it."""
    text = "Try Real Course [[id:1]] or the invented one [[id:999]]."
    out, cited = _enforce_grounding(text, allowed={1, 2})
    assert cited == [1]
    assert "[[id:999]]" not in out
    assert "[[id:1]]" in out


def test_invented_course_title_is_removed_with_its_citation():
    """Stripping only the [[id:N]] marker would leave the fabricated course
    NAME in the prose — the same false claim, just without a link on it."""
    text = "Try **Real Course** [[id:2]] for AWS, or **Nonexistent Bootcamp** [[id:9999]]."
    out, cited = _enforce_grounding(text, allowed={2})
    assert cited == [2]
    assert "Nonexistent Bootcamp" not in out
    assert "Real Course" in out
    assert out.endswith(".") and ", or ." not in out      # prose stays clean


def test_grounding_survives_a_list_with_one_invented_entry():
    out, cited = _enforce_grounding(
        "- **A** [[id:1]]\n- **Fake** [[id:77]]\n- **B** [[id:3]]", allowed={1, 3})
    assert cited == [1, 3]
    assert "Fake" not in out
    assert "**A**" in out and "**B**" in out


def test_grounding_keeps_citation_order_and_dedupes():
    out, cited = _enforce_grounding("A [[id:3]] B [[id:1]] C [[id:3]]", allowed={1, 3})
    assert cited == [3, 1]
    assert out


def test_grounding_on_text_with_no_citations_is_a_no_op():
    out, cited = _enforce_grounding("Plain career advice, no courses.", allowed={1})
    assert cited == []
    assert out == "Plain career advice, no courses."


def test_offline_reply_only_cites_supplied_courses():
    class P:
        def __init__(self, i):
            self.id, self.title, self.category = i, f"Course {i}", "AI"
            self.level, self.price = "beginner", 0

    reply = _offline_reply("anything", [P(7), P(8)], "")
    _, cited = _enforce_grounding(reply, allowed={7, 8})
    assert set(cited) <= {7, 8}


def test_offline_reply_with_no_courses_invents_nothing():
    reply = _offline_reply("quantum basket weaving", [], "")
    _, cited = _enforce_grounding(reply, allowed=set())
    assert cited == []


# --- FTS query building ------------------------------------------------------

def test_fts_query_neutralizes_operators():
    """A chat message is a sentence; an apostrophe must not raise in FTS5."""
    q = fts_query("what's the difference between RAG and fine-tuning?")
    assert '"' in q and "'" not in q
    assert " OR " in q


@pytest.mark.parametrize("raw", [
    'x" OR products_fts MATCH "y',      # quote-escape attempt
    "NEAR(a b)", "col:value", "a*", "-term", "((()))",
])
def test_fts_query_is_safe_on_hostile_input(raw):
    q = fts_query(raw)
    # Every emitted token is individually quoted, so nothing can be read as an
    # operator. Empty is a valid, safe result.
    assert q == "" or all(part.strip().startswith('"') and part.strip().endswith('"')
                          for part in q.split(" OR "))


def test_fts_query_drops_stopwords_and_returns_empty_when_nothing_useful():
    assert fts_query("what should i do") == ""
    assert fts_query("") == ""


def test_fts_query_keeps_meaningful_terms():
    q = fts_query("I want to learn langgraph and mlops")
    assert '"langgraph"' in q and '"mlops"' in q
    assert '"want"' not in q          # stopword


# --- HTTP --------------------------------------------------------------------

@pytest.fixture
async def client():
    from sqlalchemy import delete, select

    from app.db.models import ChatMessage, Conversation, Event, ResumeAnalysis, User, UserProfile
    from app.db.session import async_session

    created: list[str] = []
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        c._created_emails = created          # noqa: SLF001
        yield c

    async with async_session() as s:
        for addr in created:
            user = (await s.execute(
                select(User).where(User.email == addr))).scalar_one_or_none()
            if user is None:
                continue
            conv_ids = (await s.execute(select(Conversation.id).where(
                Conversation.user_id == user.id))).scalars().all()
            if conv_ids:
                await s.execute(delete(ChatMessage).where(
                    ChatMessage.conversation_id.in_(conv_ids)))
                await s.execute(delete(Conversation).where(
                    Conversation.id.in_(conv_ids)))
            await s.execute(delete(ResumeAnalysis).where(
                ResumeAnalysis.user_id == user.id))
            await s.execute(delete(Event).where(Event.user_id == user.id))
            await s.execute(delete(UserProfile).where(
                UserProfile.user_id == user.id))
            await s.execute(delete(User).where(User.id == user.id))
        await s.commit()


async def _register(c) -> str:
    email = f"c_{uuid.uuid4().hex[:10]}@example.com"
    await c.post("/auth/register", data={"email": email, "password": "hunter2hunter2"})
    c._created_emails.append(email)          # noqa: SLF001
    return email


@pytest.mark.asyncio
async def test_chat_requires_login(client):
    r = await client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_history_requires_login(client):
    assert (await client.get("/api/chat/history")).status_code == 401


@pytest.mark.asyncio
async def test_chat_round_trip_persists_and_replays(client):
    await _register(client)
    r = await client.post("/api/chat", json={"message": "What should I learn next?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"]
    assert body["conversation_id"]

    # Every card corresponds to a citation the answer actually made.
    for card in body["cards"]:
        assert f"[[id:{card['id']}]]" in body["answer"]

    hist = (await client.get("/api/chat/history")).json()
    roles = [m["role"] for m in hist["messages"]]
    assert roles[:2] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_empty_message_is_rejected(client):
    await _register(client)
    assert (await client.post("/api/chat", json={"message": ""})).status_code == 422


@pytest.mark.asyncio
async def test_overlong_message_is_rejected(client):
    await _register(client)
    r = await client.post("/api/chat", json={"message": "x" * 5000})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_reset_clears_the_thread(client):
    await _register(client)
    await client.post("/api/chat", json={"message": "hello there"})
    assert (await client.post("/api/chat/reset")).status_code == 200
    assert (await client.get("/api/chat/history")).json()["messages"] == []


@pytest.mark.asyncio
async def test_cannot_read_another_users_conversation(client):
    """An unchecked conversation_id would let anyone read any thread."""
    await _register(client)
    first = (await client.post("/api/chat", json={"message": "private question"})).json()

    await client.post("/auth/logout")
    await _register(client)
    r = await client.post("/api/chat", json={"message": "hi",
                                             "conversation_id": first["conversation_id"]})
    assert r.status_code == 200
    # Ownership failed, so a NEW conversation was started rather than the
    # other user's thread being joined.
    assert r.json()["conversation_id"] != first["conversation_id"]
