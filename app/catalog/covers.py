"""Generated cover art for courses — `python -m app.catalog.covers`.

Why generated rather than searched: a DuckDuckGo image search for "LLMOps
bootcamp" returns other people's copyrighted stock photos, redistributed from
this repo, that mostly show laptops. Neither the licensing nor the relevance
survives scrutiny. A cover drawn from the course's own metadata is legally
clean, always on-topic, works offline in CI, and is identical on every machine.

Why SVG rather than PNG: no dependency. Pillow is a binary wheel to install and
pin for output that is four rectangles and some text; SVG is a string this
module can build, it stays sharp at any size, and each file is ~2 KB.

The design carries information rather than decorating:

  * hue comes from the CATEGORY, so a browse page reads as colour-coded groups
  * the glyph pattern is seeded by the SLUG, so two courses in one category are
    still tellable apart at a glance
  * level and price band are drawn as text, because they are what a user
    filters on

Deterministic: same slug and category in, byte-identical file out. Re-running
overwrites in place and produces no diff, so covers can be regenerated freely.
"""
from __future__ import annotations

import hashlib
import html
import pathlib
import re

# Written into the repo, not into data/, because these are build artifacts of
# the catalog rather than runtime user data — they are served as static assets
# and should be reviewable in a diff.
COVER_DIR = pathlib.Path("app/web/static/assets/covers")

# Category → (from, to, accent). Hand-picked rather than hashed from the
# category name: hashing gives you mud and clashes, and there are only a
# handful of categories. Ordered dark→light so white text always clears WCAG AA
# against the darker stop.
PALETTES: dict[str, tuple[str, str, str]] = {
    "GenAI / LLM Engineering": ("#1a2f6b", "#3f5fd6", "#8fd0ff"),
    "Agentic AI":              ("#2b1259", "#6b35c9", "#c9a6ff"),
    "MLOps / LLMOps":          ("#0f3b34", "#1c7d63", "#7fe3c4"),
    "Data Science":            ("#5c2110", "#c05a25", "#ffc48f"),
    "Cloud":                   ("#123c52", "#2278a3", "#9adcf5"),
}
FALLBACK = ("#242a38", "#4a5468", "#b8c2d6")

LEVEL_LABEL = {"beginner": "BEGINNER", "intermediate": "INTERMEDIATE",
               "advanced": "ADVANCED"}


def palette(category: str) -> tuple[str, str, str]:
    """Exact match, then prefix match, then fallback.

    Prefix matching means a new secondary category like "GenAI / LLM
    Engineering (Advanced)" inherits the right hue instead of dropping to grey.
    """
    if category in PALETTES:
        return PALETTES[category]
    for name, colors in PALETTES.items():
        if category and (category.startswith(name.split(" /")[0])):
            return colors
    return FALLBACK


def _seed(slug: str) -> int:
    return int(hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12], 16)


def _wrap(text: str, width: int, max_lines: int) -> list[str]:
    """Greedy word wrap. Titles here run to 90+ characters, so this is load
    bearing: unwrapped, the cover would render one clipped line."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > sum(len(x) for x in lines):
        lines[-1] = lines[-1][:width - 1].rstrip() + "…"
    return lines


def _glyphs(slug: str, accent: str) -> str:
    """A deterministic scatter of circles and bars, seeded by the slug.

    This is the part that makes two courses in the same category distinguishable
    without reading the title — a page of twelve identical blue rectangles is
    worse than no image at all.
    """
    rng = _seed(slug)
    out = []
    for i in range(7):
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        x = 430 + (rng % 170)
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        y = 20 + (rng % 260)
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        size = 10 + (rng % 46)
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        opacity = 0.06 + (rng % 14) / 100
        if i % 3 == 0:
            out.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" '
                       f'rx="4" fill="{accent}" opacity="{opacity:.2f}"/>')
        else:
            out.append(f'<circle cx="{x}" cy="{y}" r="{size // 2}" '
                       f'fill="{accent}" opacity="{opacity:.2f}"/>')
    return "\n    ".join(out)


def build_svg(*, title: str, category: str, level: str, price: float,
              slug: str, mentor: str = "") -> str:
    """One cover as an SVG string. 600×300, the 2:1 ratio the cards use."""
    dark, light, accent = palette(category or "")
    lines = _wrap(title or slug, width=30, max_lines=3)
    gid = re.sub(r"[^a-z0-9]", "", slug.lower())[:24] or "cover"

    title_svg = "\n    ".join(
        f'<text x="44" y="{132 + i * 40}" font-family="Segoe UI,Helvetica,Arial,sans-serif" '
        f'font-size="31" font-weight="700" fill="#ffffff">{html.escape(line)}</text>'
        for i, line in enumerate(lines)
    )

    price_text = "FREE" if not price else f"₹{price:,.0f}"
    level_text = LEVEL_LABEL.get((level or "").lower(), (level or "").upper())
    footer = " · ".join(x for x in (level_text, price_text) if x)

    # Built separately rather than inlined as a conditional expression in the
    # template — an f-string branch that long is unreadable and unlintable.
    mentor_svg = ""
    if mentor:
        mentor_svg = (
            '<text x="556" y="264" text-anchor="end" '
            'font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="13" '
            f'fill="#ffffff" opacity="0.6">{html.escape(mentor)}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 300" \
width="600" height="300" role="img" aria-label="{html.escape(title or slug)}">
  <defs>
    <linearGradient id="g{gid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{dark}"/>
      <stop offset="100%" stop-color="{light}"/>
    </linearGradient>
  </defs>
  <rect width="600" height="300" fill="url(#g{gid})"/>
  <g>
    {_glyphs(slug, accent)}
  </g>
  <rect x="44" y="42" width="46" height="4" rx="2" fill="{accent}"/>
  <text x="44" y="74" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="13"
        font-weight="600" letter-spacing="2.4" fill="{accent}">\
{html.escape((category or "COURSE").upper())}</text>
  <g>
    {title_svg}
  </g>
  <text x="44" y="264" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="14"
        font-weight="600" letter-spacing="1.4" fill="#ffffff" opacity="0.82">\
{html.escape(footer)}</text>
  {mentor_svg}
</svg>
"""


def cover_path(slug: str) -> pathlib.Path:
    return COVER_DIR / f"{slug}.svg"


def write_cover(*, slug: str, title: str, category: str, level: str,
                price: float, mentor: str = "") -> pathlib.Path:
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    path = cover_path(slug)
    path.write_text(build_svg(title=title, category=category, level=level,
                              price=price, slug=slug, mentor=mentor),
                    encoding="utf-8")
    return path


async def generate_all(dirpath: str = "data/data_1") -> dict:
    """Generate a cover for every course in the catalog directory.

    Reads the JSON rather than the products table so covers can be built before
    (or without) a database — the same reason sync_sql exists separately from
    ingest.
    """
    from app.catalog.loader import load_all

    written = []
    for course in load_all(dirpath):
        mentors = course.get("mentors") or []
        written.append(write_cover(
            slug=course["slug"],
            title=course.get("title") or course["slug"],
            category=course.get("category") or "",
            level=course.get("level") or "",
            price=float(course.get("price") or 0),
            mentor=mentors[0] if mentors else "",
        ))
    return {"written": len(written), "dir": str(COVER_DIR)}


if __name__ == "__main__":
    import asyncio

    report = asyncio.run(generate_all())
    try:
        print(f"✓ wrote {report['written']} covers to {report['dir']}")
    except UnicodeEncodeError:
        print(f"wrote {report['written']} covers to {report['dir']}")
