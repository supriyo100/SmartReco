"""SMTP transport. One function — `send` — and a deliberate fallback.

Two decisions here are worth stating, because both are about a scheduler job
that runs unattended at 16:00.

**Missing credentials are a mode, not a failure.** If SMTP is unconfigured the
message is written to `data/outbox_mail/` and reported as `stored`. A demo, a
test run, and CI all work with no secret in the environment, and a password
that expires degrades to "the mail is on disk" instead of an exception buried
in an APScheduler log. `SendResult.ok` is true in both cases because in both
cases the caller's work is done; `SendResult.mode` says which happened, and
that is what `DigestLog.status` records.

**Failures return, they do not raise.** The digest loops over users. One bad
address must not abort the other forty-nine sends, so every error is caught and
returned as a value. The caller decides what a failure means.

Nothing here logs SMTP_PASS, and `__repr__` on the settings object is never
interpolated into a log line.
"""
from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.config import settings

log = logging.getLogger("mail")

# Where messages go when SMTP is unconfigured. Gitignored alongside the other
# runtime artifacts under data/.
MAIL_DIR = pathlib.Path("data/outbox_mail")

# A header value containing CR or LF lets a caller inject extra headers into
# the message. Subjects here are built from user-controlled data (names, course
# titles), so they are stripped rather than trusted.
_HEADER_UNSAFE = re.compile(r"[\r\n]+")


def _clean_header(value: str) -> str:
    return _HEADER_UNSAFE.sub(" ", (value or "").strip())[:400]


@dataclass(frozen=True)
class SendResult:
    """Outcome of one send. `ok` means the caller's job is done."""
    ok: bool
    mode: str          # "sent" | "stored" | "failed"
    detail: str = ""

    @property
    def sent(self) -> bool:
        """True only for a real SMTP delivery — used where the distinction
        matters, e.g. deciding whether to claim an email was delivered."""
        return self.mode == "sent"


def build_message(to: str, subject: str, html: str, text: str) -> EmailMessage:
    """Assemble a multipart/alternative message.

    Both parts are always sent. The plain-text half is not a formality: it is
    what a screen reader, a text-mode client, and most spam filters read, and a
    single-part HTML mail scores measurably worse on delivery.
    """
    msg = EmailMessage()
    msg["From"] = formataddr((settings.MAIL_FROM_NAME, settings.mail_from))
    msg["To"] = _clean_header(to)
    msg["Subject"] = _clean_header(subject)
    msg["Message-ID"] = make_msgid(domain="smartreco.local")
    msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    # Marks this as bulk/automated so replies and vacation responders behave.
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text or "")
    msg.add_alternative(html or "", subtype="html")
    return msg


def _store(msg: EmailMessage, to: str) -> SendResult:
    """Write the message to disk as a .eml — openable in any mail client."""
    try:
        MAIL_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", to)[:60] or "unknown"
        path = MAIL_DIR / f"{stamp}_{safe}.eml"
        path.write_bytes(bytes(msg))
    except OSError as exc:
        log.warning("mail: could not store message for %s: %s", to, exc)
        return SendResult(False, "failed", f"store failed: {exc}")
    log.info("mail: SMTP not configured — stored %s", path)
    return SendResult(True, "stored", str(path))


async def send(to: str, subject: str, html: str, text: str) -> SendResult:
    """Deliver one message. Never raises.

    STARTTLS on 587 (Gmail's submission port) and implicit TLS on 465 are both
    supported, chosen by port, because those are the two configurations anyone
    is likely to paste into .env.
    """
    to = (to or "").strip()
    if not to or "@" not in to:
        return SendResult(False, "failed", "no valid recipient")

    msg = build_message(to, subject, html, text)

    if not settings.smtp_configured:
        return _store(msg, to)

    import aiosmtplib

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASS,
            start_tls=settings.SMTP_PORT == 587,
            use_tls=settings.SMTP_PORT == 465,
            timeout=30,
        )
    except Exception as exc:
        # Deliberately broad: aiosmtplib raises a dozen exception types plus
        # anything the socket layer produces, and a digest loop must survive
        # all of them. The type name is kept so the cause is still diagnosable.
        detail = f"{type(exc).__name__}: {exc}"
        log.warning("mail: send to %s failed — %s", to, detail)
        return SendResult(False, "failed", detail)

    log.info("mail: sent %r to %s", _clean_header(subject)[:60], to)
    return SendResult(True, "sent")
