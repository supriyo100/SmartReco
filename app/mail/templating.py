"""Jinja environment for email — deliberately separate from the web one.

`app/web/templating.py` renders pages for a browser. Email is a different
target and sharing one environment would force both to compromise:

  * Email clients strip `<style>` blocks and ignore external stylesheets, so
    every rule has to be an inline `style=` attribute. Gmail and Outlook are
    the constraint, not a modern browser.
  * The web templates take a `request` and read identity off it. There is no
    request when a scheduler job renders a digest at 16:00.
  * Links must be absolute. A `href="/course/x"` is dead in an inbox.

So: its own loader, its own globals, and a `url` filter that makes absolute
links the path of least resistance.
"""
from __future__ import annotations

import pathlib

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def absolute(path: str) -> str:
    """Relative path → absolute URL using PUBLIC_BASE_URL."""
    if not path:
        return settings.PUBLIC_BASE_URL
    if path.startswith(("http://", "https://", "mailto:")):
        return path
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/{path.lstrip('/')}"


def money(value) -> str:
    """Price for an inbox. Free is worth saying in words — '₹0' reads like a
    rendering bug, 'Free' reads like an offer."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    return "Free" if amount <= 0 else f"₹{amount:,.0f}"


env.filters["url"] = absolute
env.filters["money"] = money
env.globals["base_url"] = lambda: settings.PUBLIC_BASE_URL.rstrip("/")
env.globals["brand"] = settings.MAIL_FROM_NAME


def render_pair(name: str, **ctx) -> tuple[str, str]:
    """Render one message as (html, text).

    Both halves come from templates rather than the text half being generated
    by stripping tags out of the HTML. Auto-degraded plain text is reliably
    bad — link hrefs vanish, tables collapse into a wall of words — and the
    text part is what accessibility tooling and spam filters actually read, so
    it is worth writing by hand.
    """
    html = env.get_template(f"{name}.html").render(**ctx)
    text = env.get_template(f"{name}.txt").render(**ctx)
    return html, text
