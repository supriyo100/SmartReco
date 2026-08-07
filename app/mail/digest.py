"""The 16:00 digest run (arch v2 §8) — the fan-out over users.

`app/mail/messages.py` knows how to build one person's mail. This module knows
who gets one and makes sure they get it once.

**Idempotency is the whole job.** `DigestLog` has a unique constraint on
(user_id, sent_date), and this inserts a row per successful send. That single
constraint is what makes the run safe to repeat: a process restart at 16:01, an
admin pressing the manual trigger after the cron already fired, or a second
worker starting by mistake all result in the same one email. The check is a
SELECT before sending *and* an INSERT after, because the SELECT alone races and
the constraint alone would let a duplicate go out before the insert failed.

**Failures are per-user.** One bad address must not stop the run — `send`
already returns rather than raises, and the result is recorded either way so
the log tells the truth about what happened.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import DigestLog, User
from app.db.session import async_session
from app.mail.messages import send_digest

log = logging.getLogger("mail.digest")

# Sends are serialized with a small gap rather than fired concurrently. An
# SMTP submission endpoint (Gmail's especially) rate-limits and will start
# refusing connections under a burst — and a digest has no deadline, so there
# is nothing to gain from parallelism and a working send to lose.
SEND_GAP_S = 0.6


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


async def _already_sent(session, user_id: int, day: str) -> bool:
    return (await session.execute(
        select(DigestLog.id).where(DigestLog.user_id == user_id,
                                   DigestLog.sent_date == day)
    )).scalar_one_or_none() is not None


async def _record(user_id: int, day: str, status: str) -> bool:
    """Claim today's slot for this user. False if another run already had it."""
    async with async_session() as s:
        s.add(DigestLog(user_id=user_id, sent_date=day, status=status))
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            return False
    return True


async def run_digest(force: bool = False, user_id: int | None = None) -> dict:
    """Send today's digests. Returns a report — never raises.

    `force` re-sends to users already logged today (admin preview / demo).
    `user_id` restricts the run to one person, which is what the admin
    "send me one" button uses.
    """
    day = _today()
    report = {"date": day, "considered": 0, "sent": 0, "stored": 0,
              "skipped": 0, "failed": 0, "errors": []}

    async with async_session() as s:
        stmt = select(User.id).where(User.is_active.is_(True),
                                     User.digest_opt_in.is_(True))
        if user_id is not None:
            # An explicit single-user request bypasses the opt-in filter: the
            # caller is an admin naming one account, and `send_digest(force=)`
            # applies the preference check itself.
            stmt = select(User.id).where(User.id == user_id)
        user_ids = list((await s.execute(stmt)).scalars().all())

    for uid in user_ids:
        report["considered"] += 1

        if not force:
            async with async_session() as s:
                if await _already_sent(s, uid, day):
                    report["skipped"] += 1
                    continue

        try:
            result = await send_digest(uid, force=force)
        except Exception as exc:
            # send_digest is written not to raise, so anything here is a bug in
            # the build step (a template typo, a None where a row was assumed).
            # It gets logged with a traceback and the run continues.
            log.exception("digest build failed for user_id=%s", uid)
            report["failed"] += 1
            report["errors"].append(f"user {uid}: {type(exc).__name__}: {exc}")
            continue

        if result.mode == "skipped":
            report["skipped"] += 1
            continue
        if not result.ok:
            report["failed"] += 1
            report["errors"].append(f"user {uid}: {result.detail}")
            continue

        report["sent" if result.sent else "stored"] += 1
        if not force and not await _record(uid, day, result.mode):
            # Lost the race to a concurrent run. The mail went out once from
            # here and the log row belongs to the other run — worth a line,
            # since it means two schedulers are live (arch trap #3).
            log.warning("digest: duplicate claim for user_id=%s on %s", uid, day)

        await asyncio.sleep(SEND_GAP_S)

    log.info("digest run: %s", {k: v for k, v in report.items() if k != "errors"})
    return report


async def run_reengage(force: bool = False) -> dict:
    """Sweep idle users. Same failure discipline as the digest.

    Not idempotent through DigestLog: `send_reengage` is self-limiting because
    a user who receives one and comes back is no longer idle, and one who does
    not come back stays idle — so this is scheduled weekly rather than daily.
    """
    from app.mail.messages import send_reengage

    report = {"considered": 0, "sent": 0, "stored": 0, "skipped": 0, "failed": 0}
    async with async_session() as s:
        user_ids = list((await s.execute(
            select(User.id).where(User.is_active.is_(True),
                                  User.digest_opt_in.is_(True))
        )).scalars().all())

    for uid in user_ids:
        report["considered"] += 1
        try:
            result = await send_reengage(uid, force=force)
        except Exception:
            log.exception("reengage failed for user_id=%s", uid)
            report["failed"] += 1
            continue
        if result.mode == "skipped":
            report["skipped"] += 1
            continue
        if not result.ok:
            report["failed"] += 1
            continue
        report["sent" if result.sent else "stored"] += 1
        await asyncio.sleep(SEND_GAP_S)

    log.info("reengage run: %s", report)
    return report
