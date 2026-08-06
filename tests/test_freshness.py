"""Tests for the live-vs-recorded + recency scoring (data/COURSE_SCHEMA.md §6).

Every test pins a fixed `today`, because a test that reads the clock passes in
August and fails in October — and this module's whole job is dates.
"""
from datetime import date

import pytest

from app.catalog.chunker import build_chunks, generate_context
from app.catalog.freshness import effective_mode, freshness_label, score_freshness

TODAY = date(2026, 8, 6)


def course(**overrides):
    base = {
        "slug": "x", "title": "X", "overview": "o", "category": "Agentic AI",
        "level": "intermediate", "is_free": False, "skills": [], "objectives": [],
        "format": {"mode": "self-paced"},
    }
    fmt = {**base["format"], **overrides.pop("format", {})}
    return {**base, **overrides, "format": fmt}


# --- the headline behaviour: live + soon beats recorded + old ----------------

def test_upcoming_live_cohort_scores_max():
    c = course(format={"mode": "live", "cohort_start": "2026-09-06"})
    assert score_freshness(c, TODAY)["score"] == 1.0


def test_live_upcoming_outranks_selfpaced_outranks_dead_cohort():
    live = course(format={"mode": "live", "cohort_start": "2026-09-06"})
    paced = course(format={"mode": "self-paced", "content_updated": "2026-06-01"})
    dead = course(format={"mode": "live", "published_at": "2025-01-25"})
    scores = [score_freshness(c, TODAY)["score"] for c in (live, paced, dead)]
    assert scores == sorted(scores, reverse=True), scores


# --- the non-obvious rule: live without a future cohort is scored as recorded -

def test_live_without_future_cohort_demoted_to_recorded():
    c = course(format={"mode": "live", "published_at": "2025-01-25"})
    mode, reason = effective_mode(c, TODAY)
    assert mode == "recorded"
    assert "no future cohort" in reason
    assert score_freshness(c, TODAY)["declared_mode"] == "live"


def test_live_with_future_cohort_keeps_live_prior():
    c = course(format={"mode": "live", "cohort_start": "2027-01-01"})
    assert effective_mode(c, TODAY)[0] == "live"


# --- recency curves ----------------------------------------------------------

def test_cohort_inside_enrollable_window_is_full_credit():
    for days in (0, 1, 30, 60):
        c = course(format={"mode": "live",
                           "cohort_start": str(date.fromordinal(TODAY.toordinal() + days))})
        assert score_freshness(c, TODAY)["recency"] == 1.0, days


def test_far_cohort_decays_on_recency_but_still_wins_on_total():
    """A cohort 12 months out scores only 0.28 recency — genuinely not
    actionable yet — which is *below* the 0.35 unknown-date floor. That is not a
    contradiction: the floor measures missing evidence, recency measures
    temporal distance, and the two aren't commensurable. The invariant that
    matters is the ranking outcome, so that is what's asserted."""
    far = course(format={"mode": "live", "cohort_start": "2027-08-06"})  # 365d out
    soon = course(format={"mode": "live", "cohort_start": "2026-09-06"})
    undated = course(format={"mode": "self-paced"})

    assert score_freshness(far, TODAY)["recency"] < 1.0
    assert score_freshness(far, TODAY)["score"] < score_freshness(soon, TODAY)["score"]
    assert score_freshness(far, TODAY)["score"] > score_freshness(undated, TODAY)["score"]


def test_started_cohort_collapses_on_30_day_half_life():
    c = course(format={"mode": "live", "cohort_start": "2026-07-07"})  # started 30d ago
    assert score_freshness(c, TODAY)["recency"] == pytest.approx(0.5, abs=0.02)


def test_content_age_uses_365_day_half_life():
    c = course(format={"mode": "self-paced", "content_updated": "2025-08-06"})
    assert score_freshness(c, TODAY)["recency"] == pytest.approx(0.5, abs=0.01)


def test_unknown_date_is_floored_not_guessed():
    c = course(format={"mode": "self-paced"})
    s = score_freshness(c, TODAY)
    assert s["recency"] == 0.35
    assert "not guessed" in s["explain"][1]


def test_content_updated_wins_over_published_at():
    c = course(format={"mode": "self-paced", "content_updated": "2026-07-01",
                       "published_at": "2020-01-01"})
    assert score_freshness(c, TODAY)["recency"] > 0.9


def test_stale_cohort_is_a_recency_input_of_last_resort():
    c = course(cohort_start_stale="2025-08-06", format={"mode": "self-paced"})
    assert score_freshness(c, TODAY)["recency"] == pytest.approx(0.5, abs=0.01)


# --- enrollment gating -------------------------------------------------------

def test_closed_enrollment_penalises_without_excluding():
    open_c = course(format={"mode": "live", "cohort_start": "2026-09-06"})
    shut = course(format={"mode": "live", "cohort_start": "2026-09-06",
                          "enrollment_status": "closed"})
    assert score_freshness(shut, TODAY)["score"] == pytest.approx(0.25)
    assert score_freshness(shut, TODAY)["score"] < score_freshness(open_c, TODAY)["score"]
    assert score_freshness(shut, TODAY)["score"] > 0  # penalty, not exclusion (B4)


def test_imminent_cohort_raises_urgency():
    assert score_freshness(course(format={"mode": "live", "cohort_start": "2026-08-10"}),
                           TODAY)["urgency"] is True
    assert score_freshness(course(format={"mode": "live", "cohort_start": "2026-09-06"}),
                           TODAY)["urgency"] is False


# --- the stale-date guarantee, all the way to rendered copy ------------------

def test_label_never_emits_a_past_date():
    """The last enforcement point of schema §3: even if a past cohort survives
    curation and the scorer, it must not reach a user as an enrolment date."""
    c = course(format={"mode": "live", "cohort_start": "2025-01-25"})
    label = freshness_label(c, TODAY)
    assert "2025-01-25" not in label
    assert label == "Recorded sessions from a previous live cohort"


def test_label_emits_future_dates():
    c = course(format={"mode": "live", "cohort_start": "2026-09-06"})
    assert "2026-09-06" in freshness_label(c, TODAY)


def test_generate_context_carries_freshness_not_stale_dates():
    c = course(price=None, price_note="check at checkout",
               format={"mode": "live", "cohort_start": "2025-01-25"})
    ctx = generate_context(c, [], set(), TODAY)
    assert "2025-01-25" not in ctx
    assert "check at checkout" in ctx      # null price renders its note, not "None"


# --- malformed input must not become a scoring input ------------------------

def test_garbage_date_is_treated_as_absent():
    c = course(format={"mode": "self-paced", "content_updated": "soon"})
    assert score_freshness(c, TODAY)["recency"] == 0.35


def test_unknown_mode_falls_back_without_raising():
    c = course(format={"mode": "webinar"})
    assert effective_mode(c, TODAY)[0] == "self-paced"


# --- chunker integration -----------------------------------------------------

def test_chunks_carry_freshness_and_omit_unknown_price():
    c = course(price=None, price_band="low", objectives=["a" * 30],
               format={"mode": "live", "cohort_start": "2026-09-06"})
    meta = next(build_chunks(c, 1, TODAY))["metadata"]
    assert meta["freshness"] == 1.0
    assert meta["mode"] == "live"
    assert "price" not in meta          # Chroma rejects None; band carries it instead
    assert meta["price_band"] == "low"
