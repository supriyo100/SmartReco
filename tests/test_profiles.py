"""Profile: declared user information (bio, goals, skills, resume).

The partial-vs-full submit distinction is the subtle part and gets the most
coverage: an upload that silently wiped a bio would be a data-loss bug that
nothing else in the suite would catch.
"""
import io
import pathlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.profiles.resume import extract_text, sanitize_filename
from app.profiles.routes import _split_skills

# --- pure helpers ------------------------------------------------------------

def test_skills_dedupe_is_case_insensitive_and_order_preserving():
    assert _split_skills("python, SQL, fastapi, Python,  sql ") == ["python", "SQL", "fastapi"]


def test_skills_handles_newlines_and_blanks():
    assert _split_skills("python\n, ,sql,") == ["python", "sql"]
    assert _split_skills("") == []
    assert _split_skills(None) == []


@pytest.mark.parametrize("name,expected", [
    # Only the basename survives, so directory components are gone entirely
    # rather than flattened into the name.
    ("../../../../etc/passwd", "passwd"),
    ("..\\..\\windows\\system32.dll", "system32.dll"),
    ("my resume (final).pdf", "my_resume__final_.pdf"),
    ("", "resume"),
    ("...", "resume"),
])
def test_sanitize_filename_strips_traversal(name, expected):
    out = sanitize_filename(name)
    assert out == expected
    assert "/" not in out and "\\" not in out and ".." not in out


def test_extract_text_reads_plain_text():
    text, warning = extract_text(b"Ada Lovelace\nSkills: Python", "cv.txt")
    assert "Ada Lovelace" in text
    assert warning == ""


def test_extract_text_collapses_whitespace_noise():
    text, _ = extract_text(b"a  \t b\n\n\n\nc", "cv.txt")
    assert text == "a b\n\nc"


def test_extract_text_never_raises_on_bad_bytes():
    text, warning = extract_text(b"\xff\xfe\x00garbage", "cv.txt")
    assert isinstance(text, str) and warning == ""


def test_unsupported_type_warns_rather_than_returning_empty_silently():
    # .docx is parsed now, so the unreadable case is a legacy .doc — the point
    # of the test is unchanged: never return "" without saying why.
    text, warning = extract_text(b"data", "resume.doc")
    assert text == ""
    assert "paste" in warning.lower()      # tells the user what to do instead


def test_docx_extracts_paragraph_text():
    """A .docx is a zip of XML; paragraph boundaries must survive as newlines,
    because the ATS section detector reads line structure."""
    import zipfile

    body = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
            '<w:p><w:r><w:t>Ada Lovelace</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Skills: Python &amp; SQL</w:t></w:r></w:p>'
            '</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", body)

    text, warning = extract_text(buf.getvalue(), "cv.docx")
    assert warning == ""
    assert "Ada Lovelace" in text
    assert "Python & SQL" in text           # XML entities decoded
    assert "\n" in text                     # paragraphs did not collapse


def test_corrupt_docx_warns_instead_of_raising():
    text, warning = extract_text(b"not a zip at all", "cv.docx")
    assert text == ""
    assert "paste" in warning.lower()


# --- HTTP --------------------------------------------------------------------

@pytest.fixture
async def client():
    """Each test registers a throwaway account; this removes it afterwards.

    Without cleanup every run leaves users, profiles and uploaded resume files
    behind in the developer's own database — the suite would slowly fill the
    app it is testing with its own debris.
    """
    from sqlalchemy import delete, select

    from app.db.models import ChatMessage, Conversation, Event, ResumeAnalysis, User, UserProfile
    from app.db.session import async_session

    created: list[str] = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._created_emails = created          # noqa: SLF001 — test-only handle
        yield c

    async with async_session() as s:
        for addr in created:
            user = (await s.execute(
                select(User).where(User.email == addr))).scalar_one_or_none()
            if user is None:
                continue
            profile = (await s.execute(select(UserProfile).where(
                UserProfile.user_id == user.id))).scalar_one_or_none()
            if profile is not None and profile.resume_path:
                pathlib.Path(profile.resume_path).unlink(missing_ok=True)
            await s.execute(delete(Event).where(Event.user_id == user.id))
            await s.execute(delete(UserProfile).where(UserProfile.user_id == user.id))
            # Children before parent: `foreign_keys=ON` is set on every connect
            # (arch §1.2), so deleting the user first fails the FK constraint
            # rather than silently orphaning rows.
            await s.execute(delete(ResumeAnalysis)
                            .where(ResumeAnalysis.user_id == user.id))
            conv_ids = (await s.execute(select(Conversation.id)
                                        .where(Conversation.user_id == user.id))).scalars().all()
            if conv_ids:
                await s.execute(delete(ChatMessage)
                                .where(ChatMessage.conversation_id.in_(conv_ids)))
                await s.execute(delete(Conversation)
                                .where(Conversation.id.in_(conv_ids)))
            await s.execute(delete(User).where(User.id == user.id))
        await s.commit()


async def _register(c) -> str:
    email = f"p_{uuid.uuid4().hex[:10]}@example.com"
    await c.post("/auth/register", data={"email": email, "password": "hunter2hunter2"})
    c._created_emails.append(email)          # noqa: SLF001
    return email


@pytest.mark.asyncio
async def test_profile_requires_login(client):
    r = await client.get("/profile")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_full_submit_saves_and_renders_back(client):
    await _register(client)
    r = await client.post("/profile", data={
        "form": "full", "full_name": "Ada Lovelace", "headline": "Backend engineer",
        "bio": "I build data pipelines.", "goals": "Move into ML engineering.",
        "skills": "python, sql", "experience_years": "3",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "Ada Lovelace" in r.text
    assert "ML engineering" in r.text


@pytest.mark.asyncio
async def test_partial_submit_does_not_wipe_untouched_fields(client):
    """The bug this guards: uploading a resume from a page whose other inputs
    are empty must not erase a bio the user never touched."""
    await _register(client)
    await client.post("/profile", data={
        "form": "full", "full_name": "Ada", "headline": "", "bio": "Distinctive bio text.",
        "goals": "Distinctive goal text.", "skills": "python", "experience_years": "3",
    }, follow_redirects=True)

    r = await client.post(
        "/profile",
        data={"full_name": "", "headline": "", "bio": "", "goals": "",
              "skills": "", "experience_years": "", "resume_text": ""},
        files={"resume": ("cv.txt", io.BytesIO(b"Ada Lovelace\nKubernetes"), "text/plain")},
        follow_redirects=True)
    assert r.status_code == 200
    assert "Distinctive bio text." in r.text
    assert "Distinctive goal text." in r.text


@pytest.mark.asyncio
async def test_full_submit_can_clear_a_field(client):
    """The other half: a cleared box on the real form must actually erase."""
    await _register(client)
    await client.post("/profile", data={
        "form": "full", "full_name": "Ada", "headline": "", "bio": "Erase me.",
        "goals": "", "skills": "", "experience_years": "",
    }, follow_redirects=True)
    r = await client.post("/profile", data={
        "form": "full", "full_name": "Ada", "headline": "", "bio": "",
        "goals": "", "skills": "", "experience_years": "",
    }, follow_redirects=True)
    assert "Erase me." not in r.text


@pytest.mark.asyncio
async def test_resume_upload_extracts_text_and_downloads_back(client):
    await _register(client)
    r = await client.post(
        "/profile",
        data={"form": "full", "full_name": "Ada", "headline": "", "bio": "", "goals": "",
              "skills": "", "experience_years": "", "resume_text": ""},
        files={"resume": ("ada_cv.txt", io.BytesIO(b"Ada\nSkills: Kubernetes"), "text/plain")},
        follow_redirects=True)
    assert "characters extracted" in r.text

    r = await client.get("/profile/resume")
    assert r.status_code == 200
    assert b"Kubernetes" in r.content


@pytest.mark.asyncio
async def test_rejected_upload_does_not_crash(client):
    await _register(client)
    r = await client.post(
        "/profile",
        data={"form": "full", "full_name": "Ada", "headline": "", "bio": "", "goals": "",
              "skills": "", "experience_years": "", "resume_text": ""},
        files={"resume": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        follow_redirects=True)
    assert r.status_code == 200
    assert "not accepted" in r.text


@pytest.mark.asyncio
async def test_traversal_filename_cannot_escape_upload_dir(client, tmp_path, monkeypatch):
    import app.profiles.routes as routes
    monkeypatch.setattr(routes, "RESUME_DIR", tmp_path / "resumes")
    await _register(client)
    await client.post(
        "/profile",
        data={"form": "full", "full_name": "E", "headline": "", "bio": "", "goals": "",
              "skills": "", "experience_years": "", "resume_text": ""},
        files={"resume": ("../../../../evil.txt", io.BytesIO(b"pwned"), "text/plain")},
        follow_redirects=True)
    written = list((tmp_path / "resumes").glob("*"))
    assert written, "nothing was written"
    for p in written:
        assert p.parent == tmp_path / "resumes"
        assert p.name.startswith("user_")
    assert not (tmp_path / "evil.txt").exists()
    assert not pathlib.Path("evil.txt").exists()


@pytest.mark.asyncio
async def test_pasted_text_wins_over_uploaded_file(client):
    await _register(client)
    r = await client.post(
        "/profile",
        data={"form": "full", "full_name": "Ada", "headline": "", "bio": "", "goals": "",
              "skills": "", "experience_years": "", "resume_text": "PASTED VERSION"},
        files={"resume": ("cv.txt", io.BytesIO(b"UPLOADED VERSION"), "text/plain")},
        follow_redirects=True)
    assert "PASTED VERSION" in r.text
    assert "UPLOADED VERSION" not in r.text
