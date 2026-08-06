"""Password hashing and the signed session cookie (arch §1.1).

Deliberately small: bcrypt for passwords, an itsdangerous signed cookie for the
session. There is no server-side session table because there is nothing in a
session worth a table — the cookie carries user_id and role, and both are cheap
to re-verify against the DB when a handler actually needs the row.
"""
from __future__ import annotations

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

COOKIE_NAME = "sr_session"
SID_COOKIE = "sid"
MAX_AGE_S = 60 * 60 * 24 * 14  # 14 days
MAX_PW_BYTES = 72              # bcrypt's hard limit

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="sr-session")


# bcrypt directly, not passlib: passlib 1.7.4 is unmaintained and its bcrypt
# backend breaks against bcrypt >= 4.1 (reads bcrypt.__about__, which is gone,
# then hands hashpw an over-length config that bcrypt 5 rejects outright).
# The API we need here is two function calls.
def hash_password(raw: str) -> str:
    # bcrypt truncates past 72 bytes; refuse rather than silently accept a
    # password whose tail does nothing.
    pw = raw.encode("utf-8")
    if len(pw) > MAX_PW_BYTES:
        raise ValueError(f"password too long (max {MAX_PW_BYTES} bytes)")
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed stored hash, or an over-length candidate — not a match,
        # and not a 500.
        return False


def sign_session(user_id: int, role: str) -> str:
    return _serializer.dumps({"uid": user_id, "role": role})


def read_session(token: str | None) -> dict | None:
    """Return {'uid', 'role'} or None. Never raises — a bad cookie is anonymous."""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE_S)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "uid" not in data:
        return None
    return data


def cookie_kwargs(request=None) -> dict:
    """Shared cookie flags.

    `secure` is decided by the request scheme, not the env name. Keying it off
    ENV alone means any non-development deployment served over plain http sets
    Secure cookies the browser then refuses to send back — login appears to
    succeed and every subsequent request is anonymous. Falling back to
    `ENV != development` only when there is no request to inspect.
    """
    if request is not None:
        # url.scheme already reflects X-Forwarded-Proto when uvicorn runs with
        # --proxy-headers, which is the usual TLS-terminating-proxy setup.
        secure = request.url.scheme == "https"
    else:
        secure = settings.ENV not in ("development", "test")
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "max_age": MAX_AGE_S,
        "path": "/",
    }
