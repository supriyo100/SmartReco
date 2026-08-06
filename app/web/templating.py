"""One Jinja2Templates instance, shared by every router that renders HTML.

Lives in app/web/ rather than being constructed per-router so that globals and
filters registered here apply everywhere.
"""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/web/templates")


def render(request, name: str, **ctx):
    """Render with the identity already in context, so base.html can draw the
    nav without every handler remembering to pass user/role.

    `role` prefers the value the middleware resolved against the DB over the
    one baked into the cookie. A cookie signed before a role change is stale —
    authorization is unaffected (current_user re-reads the row), but the nav
    would otherwise hide Admin from someone who is now an admin until they log
    out and back in.
    """
    ctx.setdefault("user_id", getattr(request.state, "user_id", None))
    ctx.setdefault("role", getattr(request.state, "role", None))
    return templates.TemplateResponse(request, name, ctx)
