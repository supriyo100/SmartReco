"""python -m app.auth.cli — create or promote an admin.

Registration always yields role='user' (arch §1.1), so without this there is no
way to mint the first admin short of hand-editing SQLite.

    python -m app.auth.cli make-admin you@example.com
    python -m app.auth.cli create-admin you@example.com --password ...
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.auth.security import hash_password
from app.db.models import User
from app.db.session import async_session


async def make_admin(email: str) -> int:
    email = email.strip().lower()
    async with async_session() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            print(f"✗ no user with email {email!r} — register first, or use create-admin")
            return 1
        if user.role == "admin":
            print(f"• {email} is already an admin")
            return 0
        user.role = "admin"
        await s.commit()
    print(f"✓ {email} promoted to admin")
    return 0


async def create_admin(email: str, password: str | None) -> int:
    email = email.strip().lower()
    password = password or getpass.getpass("password: ")
    if len(password) < 8:
        print("✗ password must be at least 8 characters")
        return 1
    try:
        pw_hash = hash_password(password)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1
    async with async_session() as s:
        if (await s.execute(select(User.id).where(User.email == email))).scalar_one_or_none():
            print(f"✗ {email} already exists — use make-admin to promote it")
            return 1
        s.add(User(email=email, password_hash=pw_hash, role="admin"))
        await s.commit()
    print(f"✓ created admin {email}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="admin user management")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("make-admin", help="promote an existing user")
    p1.add_argument("email")

    p2 = sub.add_parser("create-admin", help="create a new admin user")
    p2.add_argument("email")
    p2.add_argument("--password", default=None, help="prompted for if omitted")

    args = ap.parse_args()
    if args.cmd == "make-admin":
        return asyncio.run(make_admin(args.email))
    return asyncio.run(create_admin(args.email, args.password))


if __name__ == "__main__":
    sys.exit(main())
