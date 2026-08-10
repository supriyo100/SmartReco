"""Admin-editable secrets and models: DB-persisted, write-through to .env,
applied to the live process immediately — no restart needed.

Why not hash the Mesh key: hashing is one-way, and the key has to be sent
back to Mesh as a Bearer token on every call, so it must be recoverable, not
just verifiable. It is encrypted instead (Fernet, key derived from
SECRET_KEY) and only ever shown to the admin UI as a masked preview
(`mask()`) — the plaintext round-trips to the browser once, at the moment it
is typed into the form, and never again.

Why persist to .env AND the DB, not just one: .env is what every other
secret in this project already lives in and what a fresh `make dev` boots
from, so a restart must not silently revert an admin's change. The DB copy is
what lets a change take effect without a restart at all, and it is what
`load_persisted_settings()` replays at startup so a DB-saved value wins over
a stale .env (e.g. after a deploy resets .env from a template).
"""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.db.models import Setting
from app.db.session import async_session

log = logging.getLogger("admin.secrets")

ENV_PATH = Path(".env")

SECRET_KEYS = {"MESH_API_KEY", "SMTP_PASS"}
MODEL_KEYS = {"MODEL_FAST", "MODEL_FAST_FALLBACK", "MODEL_WRITER", "EMBED_MODEL"}
SMTP_KEYS = {"SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "MAIL_FROM", "MAIL_FROM_NAME"}


def _fernet() -> Fernet:
    # Fernet needs a 32-byte urlsafe-base64 key; derived from SECRET_KEY so
    # there is nothing new to provision or rotate separately. Any SECRET_KEY
    # change invalidates previously-encrypted rows — acceptable here since a
    # SECRET_KEY rotation already logs out every session.
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def mask(value: str) -> str:
    """Enough of the key to recognize it, never enough to use it."""
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:8]}...{value[-4:]}"


def _update_env_file(updates: dict[str, str]) -> None:
    """Rewrite KEY=VALUE lines in place; append any key .env doesn't have yet.
    Comments and unrelated lines are left untouched.

    No-ops under ENV=test: tests/conftest.py repoints DATABASE_URL/SMTP at
    throwaway targets specifically so a test run can never touch a real
    developer's files (see its comment re: 36 leaked test products). Writing
    the developer's actual .env from a settings-page test would be the same
    class of mistake, so this follows the same rule rather than needing its
    own test-isolation fixture.
    """
    if settings.ENV == "test":
        return
    remaining = dict(updates)
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


async def _persist(key: str, value: str, *, encrypted: bool) -> None:
    stored = _fernet().encrypt(value.encode()).decode() if encrypted else value
    async with async_session() as s:
        row = (await s.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if row is None:
            row = Setting(key=key)
            s.add(row)
        row.value = stored
        row.is_encrypted = encrypted
        await s.commit()


async def set_mesh_api_key(raw_key: str) -> None:
    """Persist and apply a new Mesh key everywhere it's read from: DB
    (encrypted), .env (write-through), the live `settings` singleton, and the
    cached client in app/agent/providers.py — which is otherwise built once
    and reused, so without the reset the new key would sit unused until the
    process restarted.
    """
    from app.agent.providers import reset_mesh_client

    raw_key = raw_key.strip()
    await _persist("MESH_API_KEY", raw_key, encrypted=True)
    _update_env_file({"MESH_API_KEY": raw_key})
    settings.MESH_API_KEY = raw_key
    reset_mesh_client()
    log.info("Mesh API key updated by admin (masked: %s)", mask(raw_key))


async def set_models(*, model_fast: str, model_fast_fallback: str,
                     model_writer: str, embed_model: str) -> None:
    """Model names aren't secret — persisted plain, applied in-memory
    immediately. No client reset needed: app/agent/providers.chain() reads
    settings.MODEL_* fresh on every call rather than caching them."""
    values = {
        "MODEL_FAST": model_fast.strip(),
        "MODEL_FAST_FALLBACK": model_fast_fallback.strip(),
        "MODEL_WRITER": model_writer.strip(),
        "EMBED_MODEL": embed_model.strip(),
    }
    values = {k: v for k, v in values.items() if v}
    for k, v in values.items():
        await _persist(k, v, encrypted=False)
        setattr(settings, k, v)
    _update_env_file(values)


async def set_smtp_settings(*, smtp_host: str, smtp_port: int, smtp_user: str,
                            smtp_pass: str, mail_from: str, mail_from_name: str) -> None:
    """Persist SMTP transport settings, applied to the live `settings`
    singleton immediately. No client to reset: app/mail/sender.py reads
    settings.SMTP_* fresh on every send rather than caching a connection.

    smtp_pass is optional per-call: a blank submission leaves the stored
    password untouched, so an admin can fix the host or account without
    retyping a secret that's already sitting encrypted in the DB and .env —
    mirroring how the Mesh key form treats blanks as "unchanged", not "clear".
    """
    values = {
        "SMTP_HOST": smtp_host.strip(),
        "SMTP_PORT": str(smtp_port),
        "SMTP_USER": smtp_user.strip(),
        "MAIL_FROM": mail_from.strip(),
        "MAIL_FROM_NAME": mail_from_name.strip(),
    }
    for k, v in values.items():
        await _persist(k, v, encrypted=False)
        setattr(settings, k, int(v) if k == "SMTP_PORT" else v)

    smtp_pass = smtp_pass.strip()
    if smtp_pass:
        await _persist("SMTP_PASS", smtp_pass, encrypted=True)
        settings.SMTP_PASS = smtp_pass
        values["SMTP_PASS"] = smtp_pass

    _update_env_file(values)
    log.info("SMTP settings updated by admin (host=%s, user=%s, pass %s)",
             values["SMTP_HOST"], values["SMTP_USER"],
             "changed" if smtp_pass else "unchanged")


async def load_persisted_settings() -> None:
    """Replay DB-saved overrides on top of whatever .env loaded at import
    time. Called once from the app lifespan, before the Mesh model check —
    a key saved from the admin UI must win over a stale .env."""
    async with async_session() as s:
        rows = (await s.execute(select(Setting))).scalars().all()

    changed_mesh_key = False
    for row in rows:
        if row.key not in (SECRET_KEYS | MODEL_KEYS | SMTP_KEYS):
            continue
        value = _fernet().decrypt(row.value.encode()).decode() if row.is_encrypted else row.value
        if not value:
            continue
        setattr(settings, row.key, int(value) if row.key == "SMTP_PORT" else value)
        if row.key == "MESH_API_KEY":
            changed_mesh_key = True

    if changed_mesh_key:
        from app.agent.providers import reset_mesh_client
        reset_mesh_client()
