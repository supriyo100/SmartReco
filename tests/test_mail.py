"""Mail: transport fallback, template rendering, and digest idempotency.

No SMTP server is involved. `settings.smtp_configured` is false under the test
env (conftest leaves SMTP_* unset), so `send` takes the store-to-disk path —
which is precisely the behavior worth testing, since it is what CI and a
credential-less demo run on.
"""
from __future__ import annotations

import email
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.models import (
    DigestLog,
    Event,
    Product,
    Recommendation,
    ResumeAnalysis,
    User,
    UserProfile,
)
from app.db.session import async_session
from app.mail import sender
from app.mail.templating import absolute, money, render_pair

# --- transport --------------------------------------------------------------

async def test_send_stores_when_smtp_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(sender, "MAIL_DIR", tmp_path)
    result = await sender.send("someone@example.com", "Subject here",
                               "<p>body</p>", "body")
    assert result.ok
    assert result.mode == "stored"
    assert not result.sent            # stored is not delivered — the distinction matters

    files = list(tmp_path.glob("*.eml"))
    assert len(files) == 1
    msg = email.message_from_bytes(files[0].read_bytes())
    assert msg["To"] == "someone@example.com"
    assert msg["Subject"] == "Subject here"
    assert msg.is_multipart()
    # Both alternatives present — a single-part HTML mail is what we're avoiding.
    assert {p.get_content_type() for p in msg.walk()} >= {"text/plain", "text/html"}


async def test_send_rejects_bad_recipient():
    result = await sender.send("not-an-address", "s", "<p>h</p>", "t")
    assert not result.ok
    assert result.mode == "failed"


async def test_header_injection_is_stripped(tmp_path, monkeypatch):
    """A subject built from user data must not be able to add headers."""
    monkeypatch.setattr(sender, "MAIL_DIR", tmp_path)
    await sender.send("a@example.com", "Hi\r\nBcc: victim@example.com",
                      "<p>h</p>", "t")
    msg = email.message_from_bytes(next(tmp_path.glob("*.eml")).read_bytes())
    assert msg["Bcc"] is None
    assert "\n" not in msg["Subject"]


# --- template helpers -------------------------------------------------------

def test_absolute_urls():
    assert absolute("/course/x").endswith("/course/x")
    assert absolute("/course/x").startswith("http")
    # An already-absolute URL is passed through, not double-prefixed.
    assert absolute("https://example.com/y") == "https://example.com/y"


def test_money_says_free():
    assert money(0) == "Free"
    assert money(1999) == "₹1,999"
    assert money(None) == ""


# --- rendering --------------------------------------------------------------

class _P:
    """Minimal product stand-in — the templates only touch these fields."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _card(title="Intro to RAG", slug="intro-rag"):
    return {"p": _P(title=title, slug=slug, category="GenAI", level="beginner",
                    price=1999.0),
            "item": {"hook": "You've been reading about retrieval.",
                     "reason": "Builds on the two vector-search pages you opened.",
                     "rank": 0}}


def test_digest_renders_both_parts():
    html, text = render_pair(
        "digest", greeting_name="Sam", narrative="Three that fit where you are.",
        cards=[_card()], top_title="Intro to RAG", sent_on="07 August 2026",
        missing_skills=["langgraph", "evaluation"], target_role="GenAI Engineer")

    assert "Sam" in html and "Sam" in text
    assert "Intro to RAG" in html and "Intro to RAG" in text
    # The per-user reasoning must survive into both halves — it is the thing
    # that makes this a recommendation rather than a catalog dump.
    assert "Builds on the two vector-search pages" in html
    assert "Builds on the two vector-search pages" in text
    # Links are absolute in an inbox.
    assert "http" in html and "/course/intro-rag" in html
    assert "/course/intro-rag" in text
    # Unsubscribe is present in both.
    assert "/profile" in html and "/profile" in text


def test_digest_renders_without_a_name():
    """No name must read correctly, not leave a dangling comma."""
    html, text = render_pair("digest", greeting_name="", narrative="",
                             cards=[_card()], top_title="Intro to RAG",
                             sent_on="07 August 2026", missing_skills=[],
                             target_role="")
    assert "Here's what we'd look at next" in html
    assert ", here" not in text.split("\n")[0]


def test_welcome_lists_missing_fields():
    html, text = render_pair("welcome", greeting_name="Ada", target_role="Data Engineer",
                             missing=["Target role", "Resume"], percent=40)
    assert "40% complete" in html
    assert "Target role" in html and "Resume" in html
    assert "Data Engineer" in text


def test_reengage_names_the_last_course():
    html, text = render_pair(
        "reengage", greeting_name="", last_title="Vector Databases",
        days_since=9, inactive_days=7,
        cards=[{"p": _P(title="Vector Databases", slug="vdb", category="GenAI",
                        level="intermediate", price=0.0),
                "item": None, "note": "You were reading this one."}])
    assert "Vector Databases" in html and "Vector Databases" in text
    assert "9 day" in html
    assert "Free" in html          # money filter applied in the card


def test_escaping_is_on():
    """Course titles are operator-supplied; they must not inject markup."""
    html, _ = render_pair(
        "reengage", greeting_name="", last_title="<script>alert(1)</script>",
        days_since=1, inactive_days=7, cards=[])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- message building against the DB ---------------------------------------

# Emails and slugs are suffixed per call. The suite shares one database and
# does not truncate between runs, so a fixed address collides with the row a
# previous run left behind — the failure looks like a bug in the code under
# test rather than in the fixture, which is worth spending a uuid to avoid.
def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _make_user(tag="digest", opt_in=True) -> int:
    async with async_session() as s:
        user = User(email=f"{_unique(tag)}@example.com", password_hash="x",
                    role="user", digest_opt_in=opt_in, is_active=True)
        s.add(user)
        await s.flush()
        s.add(UserProfile(user_id=user.id, full_name="Test Person"))
        await s.commit()
        return user.id


async def _make_product(slug="p1", title="Test Course") -> int:
    async with async_session() as s:
        p = Product(title=title, slug=_unique(slug), description="d",
                    category="GenAI", level="beginner", price=100.0,
                    tags=["rag", "langgraph"], is_active=True)
        s.add(p)
        await s.commit()
        return p.id


async def test_digest_skipped_without_recommendation(db):
    from app.mail.messages import send_digest

    uid = await _make_user("norec")
    result = await send_digest(uid)
    assert result.mode == "skipped"
    assert "no current recommendation" in result.detail


async def test_digest_respects_opt_out(db, tmp_path, monkeypatch):
    from app.mail.messages import send_digest

    monkeypatch.setattr(sender, "MAIL_DIR", tmp_path)
    uid = await _make_user("optout", opt_in=False)
    pid = await _make_product("optout-course")
    async with async_session() as s:
        s.add(Recommendation(user_id=uid, narrative="n", fingerprint="f",
                             is_current=True,
                             items=[{"product_id": pid, "rank": 0,
                                     "hook": "h", "reason": "r"}]))
        await s.commit()

    assert (await send_digest(uid)).mode == "skipped"
    # ...and force overrides it, which is what the admin button relies on.
    assert (await send_digest(uid, force=True)).mode == "stored"


async def test_digest_drops_deactivated_products(db):
    """A rec naming a course that has since been deactivated must not mail a
    dead link — an inbox keeps the message long after the page would refresh."""
    from app.mail.messages import build_digest

    uid = await _make_user("stale")
    pid = await _make_product("stale-course")
    async with async_session() as s:
        s.add(Recommendation(user_id=uid, narrative="n", fingerprint="f",
                             is_current=True,
                             items=[{"product_id": pid, "rank": 0}]))
        await s.commit()
    assert await build_digest(uid) is not None

    async with async_session() as s:
        product = (await s.execute(select(Product).where(Product.id == pid))).scalar_one()
        product.is_active = False
        await s.commit()
    # Every card dropped → nothing worth sending at all.
    assert await build_digest(uid) is None


async def test_digest_run_is_idempotent(db, tmp_path, monkeypatch):
    """The core guarantee: running twice mails once."""
    from app.mail.digest import run_digest

    monkeypatch.setattr(sender, "MAIL_DIR", tmp_path)
    uid = await _make_user("idem")
    pid = await _make_product("idem-course")
    async with async_session() as s:
        s.add(Recommendation(user_id=uid, narrative="n", fingerprint="f",
                             is_current=True,
                             items=[{"product_id": pid, "rank": 0,
                                     "hook": "h", "reason": "r"}]))
        await s.commit()

    # Scoped to this user: the suite shares a database, so an unscoped run
    # would also mail every user another test happened to leave behind and the
    # counts would depend on execution order.
    first = await run_digest(user_id=uid)
    assert first["stored"] == 1

    second = await run_digest(user_id=uid)
    assert second["stored"] == 0
    assert second["skipped"] == 1

    async with async_session() as s:
        rows = (await s.execute(
            select(DigestLog).where(DigestLog.user_id == uid))).scalars().all()
        assert len(rows) == 1          # one log row, not two


async def test_ats_report_maps_gaps_to_courses(db, tmp_path, monkeypatch):
    from app.mail.messages import send_ats_report

    monkeypatch.setattr(sender, "MAIL_DIR", tmp_path)
    uid = await _make_user("ats")
    await _make_product("langgraph-course")
    async with async_session() as s:
        s.add(ResumeAnalysis(user_id=uid, target_role="GenAI Engineer",
                             ats_score=62, keyword_score=55, structure_score=70,
                             experience_score=60, readability_score=80,
                             matched_skills=["python"],
                             missing_skills=["langgraph", "rag"],
                             suggestions=["Add dates to each role."],
                             is_current=True))
        await s.commit()

    result = await send_ats_report(uid)
    assert result.ok and result.mode == "stored"

    body = next(tmp_path.glob("*.eml")).read_bytes().decode("utf-8", "replace")
    assert "62" in body
    # The course whose tags cover a missing skill is surfaced — the link
    # between the two features, not just a score in isolation.
    assert "Test Course" in body


async def test_reengage_needs_something_specific_to_say(db):
    from app.mail.messages import send_reengage

    uid = await _make_user("quiet")
    # No events at all → nothing to anchor a nudge on → no send.
    result = await send_reengage(uid)
    assert result.mode == "skipped"


async def test_reengage_skips_active_users(db):
    from app.mail.messages import send_reengage

    uid = await _make_user("active")
    pid = await _make_product("active-course")
    async with async_session() as s:
        s.add(Event(event_uuid=_unique("e-active"), user_id=uid, session_id="s",
                    event_type="product_view", product_id=pid,
                    ts=datetime.utcnow()))
        await s.commit()
    result = await send_reengage(uid)
    assert result.mode == "skipped"
    assert "active within" in result.detail


async def test_reengage_fires_for_idle_user(db, tmp_path, monkeypatch):
    from app.mail.messages import send_reengage

    monkeypatch.setattr(sender, "MAIL_DIR", tmp_path)
    uid = await _make_user("idle")
    pid = await _make_product("idle-course")
    async with async_session() as s:
        s.add(Event(event_uuid=_unique("e-idle"), user_id=uid, session_id="s",
                    event_type="product_view", product_id=pid,
                    ts=datetime.utcnow() - timedelta(days=30)))
        await s.commit()

    result = await send_reengage(uid)
    assert result.ok and result.mode == "stored"
    body = next(tmp_path.glob("*.eml")).read_bytes().decode("utf-8", "replace")
    assert "Test Course" in body
