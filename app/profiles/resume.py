"""Resume intake: accept a file, keep the artifact, extract usable text.

Scope decision, stated plainly: this extracts text from .txt and .md natively,
and makes a best effort on .pdf using pypdf ONLY if it happens to be installed.
No PDF library is currently a dependency, and adding one four days from the
deadline buys less than it costs — a resume's value here is its *text*, and the
profile form has a paste box that gets that text with zero parsing risk.

The important property is that failure is LOUD. An extractor that returns "" on
a PDF it cannot read would leave the user with a green checkmark, a stored file,
and an empty profile the agent can never use. Instead the upload succeeds (the
file is kept), and the caller is handed a warning to show.
"""
from __future__ import annotations

import pathlib
import re
import unicodedata

# A resume is a page or three. The cap exists so a pathological upload cannot
# push a multi-megabyte string into an LLM prompt or a SQLite row.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024      # 2 MB
MAX_TEXT_CHARS = 20_000

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".doc", ".docx"}


def safe_suffix(filename: str) -> str:
    return pathlib.Path(filename or "").suffix.lower()


def sanitize_filename(filename: str) -> str:
    """Reduce an uploaded name to something safe to place on disk.

    Uploaded filenames are attacker-controlled: `../../etc/passwd` and embedded
    NULs are the classic cases. Taking only the basename and whitelisting
    characters removes the whole class rather than blacklisting known-bad ones.
    """
    base = pathlib.PurePosixPath(filename or "").name
    base = pathlib.PureWindowsPath(base).name          # handles a:\b\c.pdf too
    base = unicodedata.normalize("NFKD", base)
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._-")
    return base[:120] or "resume"


def _clean(text: str) -> str:
    """Collapse the whitespace noise that PDF and copy-paste extraction leave."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def extract_text(raw: bytes, filename: str) -> tuple[str, str]:
    """Returns (text, warning). A non-empty warning means text is unusable.

    Never raises on bad input — a corrupt upload is a message to the user, not
    a 500.
    """
    suffix = safe_suffix(filename)

    if suffix in TEXT_SUFFIXES or not suffix:
        try:
            return _clean(raw.decode("utf-8")), ""
        except UnicodeDecodeError:
            # Latin-1 decodes any byte string, so this cannot fail — worst case
            # it produces mojibake, which is visible to the user in the preview.
            return _clean(raw.decode("latin-1", errors="replace")), ""

    if suffix == ".pdf":
        try:
            import pypdf  # optional; not in requirements.txt
        except ImportError:
            return "", ("PDF text could not be extracted (no PDF library installed). "
                        "The file is saved, but paste the text below so it can be used.")
        try:
            import io
            reader = pypdf.PdfReader(io.BytesIO(raw))
            pages = [(p.extract_text() or "") for p in reader.pages]
            text = _clean("\n\n".join(pages))
        except Exception as exc:
            return "", (f"PDF could not be read ({type(exc).__name__}). The file is "
                        f"saved, but paste the text below so it can be used.")
        if not text:
            # A scanned resume is images; there are no glyphs to extract. Say so
            # rather than storing an empty string and calling it success.
            return "", ("No text found in that PDF — it may be a scan. The file is "
                        "saved, but paste the text below so it can be used.")
        return text, ""

    if suffix in {".doc", ".docx"}:
        return "", ("Word files are not parsed. The file is saved, but paste the "
                    "text below so it can be used.")

    return "", f"Unsupported file type {suffix!r}."
