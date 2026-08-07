"""One Jinja2Templates instance, shared by every router that renders HTML.

Lives in app/web/ rather than being constructed per-router so that globals and
filters registered here apply everywhere.
"""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/web/templates")

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
