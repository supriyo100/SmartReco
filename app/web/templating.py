"""One Jinja2Templates instance, shared by every router that renders HTML.

Lives in app/web/ rather than being constructed per-router so that globals and
filters registered here apply everywhere.
"""
import hashlib
import pathlib

from fastapi.templating import Jinja2Templates

from app.config import settings

templates = Jinja2Templates(directory="app/web/templates")

_STATIC_ROOT = pathlib.Path("app/web/static")
_hash_cache: dict[str, str] = {}


def static_url(path: str) -> str:
    """`/static/app.css` → `/static/app.css?v=<content hash>`.

    Without this the browser serves the CSS and JS it cached on the first
    visit, so a shipped fix simply does not appear until someone thinks to
    hard-refresh — which is indistinguishable from the fix not working.

    Hashing content rather than stamping mtime means the URL changes exactly
    when the bytes do: no cache churn on a redeploy that changed nothing, and
    no stale asset after an edit that happened to preserve the timestamp.

    Cached in-process outside development, because hashing every asset on
    every render is real I/O on a hot path. In development the cache is
    skipped so an edit shows up on reload, which is the whole point.
    """
    if settings.ENV != "development" and path in _hash_cache:
        return _hash_cache[path]

    file = _STATIC_ROOT / path.removeprefix("/static/")
    try:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
    except OSError:
        # A missing asset is the template's problem to show, not this
        # function's to raise — an unversioned URL still resolves or 404s
        # exactly as it would have.
        return path
    url = f"{path}?v={digest}"
    _hash_cache[path] = url
    return url


templates.env.globals["static_url"] = static_url

# Which sidebar link to mark current, keyed by URL prefix. Deriving this from
# the path in one place beats having every handler pass a `nav=` string it will
# eventually forget — a highlighted nav item that lies about where you are is
# a small bug that reads as a broken app.
NAV_PREFIXES = (
    ("/recommendations", "recs"),
    ("/profile", "profile"),
    ("/admin", "admin"),
)


def _nav_for(path: str) -> str:
    for prefix, key in NAV_PREFIXES:
        if path.startswith(prefix):
            return key
    return "home"


def render(request, name: str, **ctx):
    """Render with the identity already in context, so base.html can draw the
    nav without every handler remembering to pass user/role.

    `role` prefers the value the middleware resolved against the DB over the
    one baked into the cookie. A cookie signed before a role change is stale —
    authorization is unaffected (current_user re-reads the row), but the nav
    would otherwise hide Admin from someone who is now an admin until they log
    out and back in.

    `email` is here rather than in each handler because the sidebar shows it on
    every page; passing it per-route means the account block silently renders
    blank anywhere a handler forgot.
    """
    ctx.setdefault("user_id", getattr(request.state, "user_id", None))
    ctx.setdefault("role", getattr(request.state, "role", None))
    ctx.setdefault("email", getattr(request.state, "email", None))
    ctx.setdefault("nav", _nav_for(request.url.path))
    return templates.TemplateResponse(request, name, ctx)
