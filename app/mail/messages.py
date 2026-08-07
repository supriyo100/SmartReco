"""The four message kinds: gather the data, render, send.

Each `send_*` function is self-contained — it takes a user id, reads what it
needs, and returns a `SendResult`. That shape is what lets the same function
serve a scheduler job, an admin button, and a route handler without any of them
knowing about the others.

Two rules hold across all four.

**Opt-out is checked here, once.** Every function returns `skipped` for a user
who has `digest_opt_in` off, rather than trusting each caller to remember. The
one exception is the welcome mail, which is transactional — it is the
confirmation of an action the user just took, not marketing, and it is the mail
that tells them the preference exists in the first place.

**Deactivated products are dropped, never rendered.** A stored recommendation
can outlive the course it names (`is_active` goes false), and the web surface
already drops those cards in `app/web/routes.py`. An email is worse than a page
here: the page is re-rendered on every visit, but a link mailed on Tuesday is
still in the inbox on Friday, so a dead card is permanent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.db.models import Event, Product, Recommendation, ResumeAnalysis, User, UserProfile
from app.db.session import async_session
from app.mail.sender import SendResult, send
from app.mail.templating import render_pair

log = logging.getLogger("mail")

# Cap on courses per email. A digest that scrolls is a digest that gets
# archived — the top few are the recommendation, the rest is padding.
MAX_CARDS = 4

SKIPPED = SendResult(True, "skipped", "opted out")


def _first_name(profile: UserProfile | None, email: str) -> str:
    """A greeting name, or "" — never a mangled email local-part.

    "Hi meetsupriyochakraborty," is worse than "Hi," — it makes it obvious the
    sender knows nothing about you. So the fallback is nothing at all, and the
    templates are written to read correctly without a name.
    """
    full = (getattr(profile, "full_name", "") or "").strip()
    return full.split()[0][:40] if full else ""


async def _products_by_id(session, ids: list[int]) -> dict[int, Product]:
    if not ids:
        return {}
    rows = (await session.execute(
        select(Product).where(Product.id.in_(ids), Product.is_active.is_(True))
    )).scalars().all()
    return {p.id: p for p in rows}


async def _load_user(session, user_id: int) -> tuple[User | None, UserProfile | None]:
    user = (await session.execute(
        select(User).where(User.id == user_id))).scalar_one_or_none()
    profile = (await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id))).scalar_one_or_none()
    return user, profile


# --- 1. daily digest --------------------------------------------------------

async def build_digest(user_id: int) -> dict | None:
    """Assemble the digest context, or None if there is nothing worth sending.

    Returning None rather than an empty digest is the important behavior. A
    user with no current recommendation gets no email — an "we have nothing for
    you today" message trains people to ignore the sender, and the whole design
    of this system (§5.1) is that a recommendation is only generated when
    behavior justifies it. No recommendation means no send.
    """
    async with async_session() as s:
        user, profile = await _load_user(s, user_id)
        if user is None or not user.is_active:
            return None

        rec = (await s.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user_id, Recommendation.is_current.is_(True))
            .order_by(Recommendation.created_at.desc())
        )).scalars().first()
        if rec is None or not rec.items:
            return None

        by_id = await _products_by_id(
            s, [i.get("product_id") for i in rec.items if i.get("product_id")])
        cards = [
            {"item": item, "p": by_id[item["product_id"]]}
            for item in sorted(rec.items, key=lambda i: i.get("rank", 0))
            if item.get("product_id") in by_id
        ][:MAX_CARDS]
        if not cards:
            return None

        # The ATS gap list, when there is one, is what lets the digest say WHY
        # these courses — connecting the resume feature to the recommender
        # instead of leaving them as two unrelated tabs.
        ats = (await s.execute(
            select(ResumeAnalysis)
            .where(ResumeAnalysis.user_id == user_id, ResumeAnalysis.is_current.is_(True))
            .order_by(ResumeAnalysis.created_at.desc())
        )).scalars().first()

    return {
        "greeting_name": _first_name(profile, user.email),
        "narrative": rec.narrative or "",
        "cards": cards,
        "top_title": cards[0]["p"].title,
        "sent_on": datetime.utcnow().strftime("%d %B %Y"),
        "missing_skills": (ats.missing_skills or [])[:6] if ats else [],
        "target_role": (profile.target_role if profile else "") or "",
        "_email": user.email,
        "_opted_in": user.digest_opt_in,
    }


async def send_digest(user_id: int, force: bool = False) -> SendResult:
    """One user's digest. `force` bypasses the opt-out — used only by the admin
    preview route, so an admin can see the thing they are debugging."""
    ctx = await build_digest(user_id)
    if ctx is None:
        return SendResult(True, "skipped", "no current recommendation")
    if not ctx["_opted_in"] and not force:
        return SKIPPED

    subject = f"Your next step: {ctx['top_title']}"
    html, text = render_pair("digest", **ctx)
    return await send(ctx["_email"], subject, html, text)


# --- 2. welcome -------------------------------------------------------------

async def send_welcome(user_id: int) -> SendResult:
    """Onboarding mail. Transactional — sent regardless of `digest_opt_in`,
    because it confirms an account the person just created and is where the
    unsubscribe link is first offered."""
    from app.profiles.routes import completeness

    async with async_session() as s:
        user, profile = await _load_user(s, user_id)
        if user is None:
            return SendResult(False, "failed", "no such user")
        progress = completeness(profile)

    ctx = {
        "greeting_name": _first_name(profile, user.email),
        "target_role": (profile.target_role if profile else "") or "",
        # Three asks, not eight. A list of everything missing reads as a chore;
        # the top three read as a suggestion, and they are weight-ordered so
        # the three shown are the three that most improve recommendations.
        "missing": progress["missing"][:3],
        "percent": progress["percent"],
    }
    subject = ("Welcome to SmartReco — 2 minutes to better recommendations"
               if ctx["missing"] else "Welcome to SmartReco")
    html, text = render_pair("welcome", **ctx)
    return await send(user.email, subject, html, text)


# --- 3. ATS report ----------------------------------------------------------

async def send_ats_report(user_id: int, force: bool = False) -> SendResult:
    """Email the current resume analysis, plus courses that close its gaps."""
    from app.profiles.ats import band

    async with async_session() as s:
        user, profile = await _load_user(s, user_id)
        if user is None:
            return SendResult(False, "failed", "no such user")
        if not user.digest_opt_in and not force:
            return SKIPPED

        ats = (await s.execute(
            select(ResumeAnalysis)
            .where(ResumeAnalysis.user_id == user_id, ResumeAnalysis.is_current.is_(True))
            .order_by(ResumeAnalysis.created_at.desc())
        )).scalars().first()
        if ats is None:
            return SendResult(True, "skipped", "no current analysis")

        # Courses that close the gaps. Matched by tag/category against the
        # missing-skill list — a SQL match, not a vector search, because this
        # runs inside a request handler and must not put a retrieval round on
        # the critical path of saving a profile.
        cards = []
        missing = [m for m in (ats.missing_skills or []) if m][:6]
        if missing:
            products = (await s.execute(
                select(Product).where(Product.is_active.is_(True)))).scalars().all()
            scored = []
            for p in products:
                haystack = " ".join([
                    p.title or "", p.category or "",
                    " ".join(p.tags or []) if isinstance(p.tags, list) else "",
                ]).lower()
                hits = [m for m in missing if m.lower() in haystack]
                if hits:
                    scored.append((len(hits), p, hits))
            scored.sort(key=lambda row: (-row[0], row[1].price))
            cards = [
                {"p": p, "item": None,
                 "note": f"Covers {', '.join(hits[:3])} — on your missing list."}
                for _, p, hits in scored[:3]
            ]

    label, css = band(ats.ats_score)
    # Band colours mirror the four CSS classes in the web UI so the number
    # doesn't change meaning between the page and the inbox.
    palette = {
        "ats-strong": ("#1f7a42", "#eefaf2"),
        "ats-good": ("#2b5cd9", "#eef3fd"),
        "ats-fair": ("#a9761b", "#fdf6e9"),
        "ats-poor": ("#a02b2b", "#fdeeee"),
    }
    color, bg = palette.get(css, ("#12244a", "#f6f8fb"))

    ctx = {
        "greeting_name": _first_name(profile, user.email),
        "ats": ats,
        "band_label": label,
        "band_color": color,
        "band_bg": bg,
        "subscores": [
            ("Keyword match", ats.keyword_score),
            ("Structure", ats.structure_score),
            ("Experience evidence", ats.experience_score),
            ("Readability", ats.readability_score),
        ],
        "missing_skills": (ats.missing_skills or [])[:10],
        "matched_skills": (ats.matched_skills or [])[:10],
        "suggestions": (ats.suggestions or [])[:5],
        "target_role": ats.target_role or "",
        "cards": cards,
        "sent_on": datetime.utcnow().strftime("%d %B %Y"),
    }
    subject = f"Resume score: {ats.ats_score}/100 — {label}"
    html, text = render_pair("ats_report", **ctx)
    return await send(user.email, subject, html, text)


# --- 4. re-engagement -------------------------------------------------------

async def send_reengage(user_id: int, force: bool = False) -> SendResult:
    """Nudge a user who has gone quiet, anchored on what they last looked at.

    Sends nothing if there is nothing specific to say. A nudge that can't name
    a course the person actually opened is a "we miss you" mail, which is the
    genre people mark as spam — and a spam complaint costs the sending domain
    far more than one skipped send.
    """
    cutoff_days = settings.REENGAGE_AFTER_DAYS
    cutoff = datetime.utcnow() - timedelta(days=cutoff_days)

    async with async_session() as s:
        user, profile = await _load_user(s, user_id)
        if user is None or not user.is_active:
            return SendResult(False, "failed", "no such user")
        if not user.digest_opt_in and not force:
            return SKIPPED

        last_ts = (await s.execute(
            select(func.max(Event.ts)).where(Event.user_id == user_id))).scalar()
        if last_ts is None:
            return SendResult(True, "skipped", "no activity ever recorded")
        if last_ts > cutoff and not force:
            return SendResult(True, "skipped", f"active within {cutoff_days}d")

        # The last product they actually opened — the anchor for the whole mail.
        last_product_id = (await s.execute(
            select(Event.product_id)
            .where(Event.user_id == user_id, Event.product_id.is_not(None),
                   Event.event_type.in_(("product_view", "product_dwell", "rec_click")))
            .order_by(Event.ts.desc()).limit(1)
        )).scalar()

        last_product = None
        if last_product_id:
            last_product = (await s.execute(
                select(Product).where(Product.id == last_product_id,
                                      Product.is_active.is_(True)))).scalar_one_or_none()

        cards = []
        if last_product is not None:
            cards.append({"p": last_product, "item": None,
                          "note": "You were reading this one."})
            # Two more from the same category — the narrowest honest claim we
            # can make about someone whose only recent signal is one page view.
            siblings = (await s.execute(
                select(Product)
                .where(Product.is_active.is_(True),
                       Product.category == last_product.category,
                       Product.id != last_product.id)
                .order_by(Product.rating.desc()).limit(2)
            )).scalars().all()
            cards += [{"p": p, "item": None,
                       "note": f"Also in {p.category}."} for p in siblings]

        if not cards:
            return SendResult(True, "skipped", "nothing specific to say")

        days_since = max(1, (datetime.utcnow() - last_ts).days)

    ctx = {
        "greeting_name": _first_name(profile, user.email),
        "last_title": last_product.title if last_product is not None else "",
        "days_since": days_since,
        "inactive_days": cutoff_days,
        "cards": cards[:MAX_CARDS],
    }
    subject = (f"Still thinking about {ctx['last_title']}?"
               if ctx["last_title"] else "Picking up where you left off")
    html, text = render_pair("reengage", **ctx)
    return await send(user.email, subject, html, text)
