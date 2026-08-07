"""Mail from the command line — `python -m app.mail.cli <command>`.

Exists because the alternatives for testing a mail change are both bad: click
through the admin UI as an admin user, or wait until 16:00. Neither is a
reasonable inner loop.

    python -m app.mail.cli check
    python -m app.mail.cli send digest someone@example.com
    python -m app.mail.cli send welcome someone@example.com --print
    python -m app.mail.cli run-digest --force

`check` is the one to reach for first when mail "isn't working": it separates
the three failure modes that look identical from the outside — unconfigured,
unreachable, and rejected credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.config import settings
from app.db.models import User
from app.db.session import async_session

KINDS = ("digest", "welcome", "ats", "reengage")


def _say(line: str) -> None:
    """Print without assuming the console can encode it.

    Same guard as app/db/init_db.py: a default Windows console is cp1252 and
    raises UnicodeEncodeError on '₹' or '—', which would make a successful send
    look like a crash.
    """
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


async def _resolve_user(email: str) -> int | None:
    async with async_session() as s:
        return (await s.execute(
            select(User.id).where(User.email == email.strip().lower())
        )).scalar_one_or_none()


async def cmd_check() -> int:
    """Prove or disprove that mail can actually be delivered."""
    if not settings.smtp_configured:
        _say("SMTP is NOT configured — mail renders to data/outbox_mail/ instead.")
        _say(f"  SMTP_HOST={settings.SMTP_HOST or '(blank)'}  "
             f"SMTP_USER={settings.SMTP_USER or '(blank)'}  "
             f"SMTP_PASS={'set' if settings.SMTP_PASS else '(blank)'}")
        _say("This is a supported mode. Fill the three to send for real.")
        return 0

    import aiosmtplib

    _say(f"Connecting to {settings.SMTP_HOST}:{settings.SMTP_PORT} as {settings.SMTP_USER} ...")
    client = aiosmtplib.SMTP(hostname=settings.SMTP_HOST, port=settings.SMTP_PORT,
                             start_tls=settings.SMTP_PORT == 587,
                             use_tls=settings.SMTP_PORT == 465, timeout=20)
    try:
        await client.connect()
    except Exception as exc:
        _say(f"  CONNECT FAILED: {type(exc).__name__}: {exc}")
        _say("  The host/port is wrong, or the network is blocking outbound SMTP.")
        return 1
    try:
        await client.login(settings.SMTP_USER, settings.SMTP_PASS)
    except Exception as exc:
        _say(f"  AUTH FAILED: {type(exc).__name__}: {exc}")
        _say("  For Gmail this is almost always an app password problem: SMTP_PASS must be "
             "the 16-character App Password, not the account password.")
        await client.quit()
        return 1
    await client.quit()
    _say("  OK — connected and authenticated. Mail will be delivered for real.")
    _say(f"  From: {settings.MAIL_FROM_NAME} <{settings.mail_from}>")
    _say(f"  Links in mail resolve against {settings.PUBLIC_BASE_URL}")
    if "localhost" in settings.PUBLIC_BASE_URL:
        _say("  NOTE: localhost links only work on this machine. Set PUBLIC_BASE_URL "
             "before mailing anyone else.")
    return 0


async def cmd_send(kind: str, email: str, show: bool) -> int:
    from app.mail import preview
    from app.mail.messages import send_ats_report, send_digest, send_reengage, send_welcome

    user_id = await _resolve_user(email)
    if user_id is None:
        _say(f"No user with email {email!r}.")
        return 1

    if show:
        html = await preview.render_preview(kind, user_id)
        if html is None:
            _say(f"{kind}: nothing to render for {email} — precondition not met.")
            return 1
        _say(html)
        return 0

    senders = {
        "digest": lambda uid: send_digest(uid, force=True),
        "welcome": send_welcome,
        "ats": lambda uid: send_ats_report(uid, force=True),
        "reengage": lambda uid: send_reengage(uid, force=True),
    }
    result = await senders[kind](user_id)
    if not result.ok:
        _say(f"{kind}: FAILED — {result.detail}")
        return 1
    if result.mode == "skipped":
        _say(f"{kind}: nothing to send — {result.detail}")
        return 0
    if result.mode == "stored":
        _say(f"{kind}: SMTP unconfigured, wrote {result.detail}")
        return 0
    _say(f"{kind}: sent to {email}")
    return 0


async def cmd_run_digest(force: bool) -> int:
    from app.mail.digest import run_digest

    report = await run_digest(force=force)
    _say(f"digest {report['date']} — considered {report['considered']}, "
         f"sent {report['sent']}, stored {report['stored']}, "
         f"skipped {report['skipped']}, failed {report['failed']}")
    for err in report["errors"][:10]:
        _say(f"  ! {err}")
    return 1 if report["failed"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.mail.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify SMTP connectivity and credentials")

    p_send = sub.add_parser("send", help="send one message to one user")
    p_send.add_argument("kind", choices=KINDS)
    p_send.add_argument("email")
    p_send.add_argument("--print", dest="show", action="store_true",
                        help="render the HTML to stdout instead of sending")

    p_run = sub.add_parser("run-digest", help="run the daily fan-out now")
    p_run.add_argument("--force", action="store_true",
                       help="re-send to users already mailed today")

    args = parser.parse_args()
    if args.command == "check":
        return asyncio.run(cmd_check())
    if args.command == "send":
        return asyncio.run(cmd_send(args.kind, args.email, args.show))
    return asyncio.run(cmd_run_digest(args.force))


if __name__ == "__main__":
    sys.exit(main())
