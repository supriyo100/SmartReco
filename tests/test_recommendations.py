"""The recommendation pipeline: ranking, grounding, triggers.

The bug these exist to prevent recurring: a user uploaded a resume, set a
target role, and got an empty page — because nothing turned those facts into a
recommendation. So the load-bearing test here is
`test_declared_profile_alone_is_enough_to_trigger`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.agent.nodes import fusion_rank, generate, grade, validate
from app.db.models import Event, Product, Recommendation, ResumeAnalysis, User, UserProfile
from app.db.session import async_session


class _P:
    """Product stand-in for the pure scoring functions."""
    def __init__(self, id=1, title="T", category="GenAI", level="intermediate",
                 price=5000.0, tags=None, description="", rating=4.5,
                 slug=None, prereq_ids=None, related_ids=None):
        self.id, self.title, self.category = id, title, category
        self.level, self.price, self.rating = level, price, rating
        self.tags = tags or []
        self.description = description
        self.slug = slug or f"s{id}"
        self.prereq_ids = prereq_ids or []
        self.related_ids = related_ids or []


# --- scoring terms ----------------------------------------------------------

def test_gap_match_rewards_covering_missing_skills():
    covers = _P(tags=["rag", "langgraph"], description="retrieval augmented")
    unrelated = _P(tags=["excel"], description="spreadsheets")
    missing = ["rag", "langgraph", "embeddings"]
    assert fusion_rank.gap_match(covers, missing) > fusion_rank.gap_match(unrelated, missing)


def test_gap_match_is_zero_without_a_resume():
    """No ATS run means no gap evidence — the term must not invent any."""
    assert fusion_rank.gap_match(_P(tags=["rag"]), []) == 0.0


def test_level_fit_prefers_the_right_difficulty():
    beginner, advanced = _P(level="beginner"), _P(level="advanced")
    # 1 year of experience
    assert fusion_rank.level_fit(beginner, 1) > fusion_rank.level_fit(advanced, 1)
    # 10 years
    assert fusion_rank.level_fit(advanced, 10) > fusion_rank.level_fit(beginner, 10)


def test_unrated_courses_are_average_not_bad():
    """Half this catalog publishes no rating. Scoring those 0 would bury them
    for a fact about our data rather than about the course."""
    assert fusion_rank.rating_prior(_P(rating=None)) == 0.5
    assert fusion_rank.rating_prior(_P(rating=5.0)) == 1.0


def test_graph_adjacency_uses_the_real_ladder():
    course = _P(prereq_ids=["foundations"], related_ids=["sibling"])
    assert fusion_rank.graph_adjacency(course, {"foundations"}) == 1.0
    assert fusion_rank.graph_adjacency(course, {"sibling"}) == 0.6
    assert fusion_rank.graph_adjacency(course, {"unrelated"}) == 0.0


def test_weights_sum_to_one():
    """A ranking whose weights do not normalise produces scores that cannot be
    compared to a threshold or to each other across runs."""
    assert abs(sum(fusion_rank.WEIGHTS.values()) - 1.0) < 1e-9


# --- confidence -------------------------------------------------------------

def test_confidence_rewards_evidence_over_popularity():
    """A course picked because it closes a real gap should outrank one picked
    because the catalog likes it."""
    evidenced = generate.confidence(
        {"gap_match": 1.0, "interest_match": 0.5, "level_fit": 1.0}, 0.6)
    generic = generate.confidence(
        {"gap_match": 0.0, "interest_match": 0.0, "level_fit": 1.0}, 0.6)
    assert evidenced > generic


def test_confidence_stays_in_range():
    assert 0.0 < generate.confidence({}, 0.0) <= 1.0
    assert 0.0 < generate.confidence({"gap_match": 1.0}, 1.0) <= 1.0


# --- grade ------------------------------------------------------------------

def test_grade_asks_for_a_retry_when_nothing_is_personal():
    ranked = [{"terms": {"gap_match": 0.0, "interest_match": 0.0,
                         "graph_adjacency": 0.0}, "score": 0.3}]
    verdict = grade.run(ranked, {}, round_number=1)
    assert verdict["retry"] is True


def test_grade_accepts_a_set_with_evidence():
    ranked = [{"terms": {"gap_match": 1.0, "interest_match": 0.4,
                         "graph_adjacency": 0.0}, "score": 0.7}]
    assert grade.run(ranked, {}, round_number=1)["retry"] is False


def test_grade_never_retries_a_cold_start():
    """Cold start has no evidence by definition; retrying returns the same
    catalog spread twice."""
    ranked = [{"terms": {}, "score": 0.2}]
    assert grade.run(ranked, {"cold_start": True})["retry"] is False


def test_grade_stops_at_the_round_cap():
    ranked = [{"terms": {}, "score": 0.1}]
    assert grade.run(ranked, {}, round_number=grade.MAX_ROUNDS)["retry"] is False


# --- validate: the grounding guarantee --------------------------------------

async def _make_product(**kw) -> Product:
    async with async_session() as s:
        p = Product(title=kw.get("title", "Real Course"),
                    slug=f"v-{uuid.uuid4().hex[:8]}", description="d",
                    category="GenAI", level="intermediate", price=1000.0,
                    is_active=kw.get("is_active", True))
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return p


async def test_validate_drops_ids_retrieval_never_produced(db):
    """The core promise: the model cannot introduce a course."""
    real = await _make_product()
    items = [{"product_id": real.id, "rank": 0, "hook": "h", "reason": "r"},
             {"product_id": 999999, "rank": 1, "hook": "invented", "reason": "r"}]
    clean, _, problems = await validate.run(items, {real.id}, "")
    assert [i["product_id"] for i in clean] == [real.id]
    assert any("ungrounded" in p for p in problems)


async def test_validate_drops_deactivated_courses(db):
    """A course deactivated between retrieval and generation must not be
    stored — a rec row outlives the request that made it."""
    dead = await _make_product(is_active=False)
    clean, _, problems = await validate.run(
        [{"product_id": dead.id, "rank": 0, "hook": "h", "reason": "r"}],
        {dead.id}, "")
    assert clean == []
    assert any("inactive" in p for p in problems)


async def test_validate_dedupes_and_renumbers(db):
    real = await _make_product()
    items = [{"product_id": real.id, "rank": 0, "hook": "a", "reason": "r"},
             {"product_id": real.id, "rank": 1, "hook": "b", "reason": "r"}]
    clean, _, problems = await validate.run(items, {real.id}, "")
    assert len(clean) == 1
    assert clean[0]["rank"] == 0
    assert any("duplicate" in p for p in problems)


async def test_validate_removes_an_invented_course_title(db):
    """A model can name a course we don't sell in prose without citing an id."""
    real = await _make_product(title="Real Course")
    narrative = 'Start here, then try "Advanced Kubernetes Bootcamp" after.'
    _, clean, problems = await validate.run(
        [{"product_id": real.id, "rank": 0, "hook": "h", "reason": "r"}],
        {real.id}, narrative)
    assert "Advanced Kubernetes Bootcamp" not in clean
    assert any("invented" in p for p in problems)


async def test_validate_leaves_ordinary_quotes_alone(db):
    real = await _make_product()
    narrative = 'It is the "right" next step.'
    _, clean, _ = await validate.run(
        [{"product_id": real.id, "rank": 0, "hook": "h", "reason": "r"}],
        {real.id}, narrative)
    assert '"right"' in clean


# --- triggers: the planner --------------------------------------------------

async def _make_user(*, declared=False, events=0) -> int:
    async with async_session() as s:
        user = User(email=f"rec-{uuid.uuid4().hex[:8]}@example.com",
                    password_hash="x", is_active=True)
        s.add(user)
        await s.flush()
        s.add(UserProfile(
            user_id=user.id,
            target_role="Generative AI Engineer" if declared else "",
            goals="build LLM products" if declared else "",
        ))
        for i in range(events):
            s.add(Event(event_uuid=f"{uuid.uuid4().hex}", user_id=user.id,
                        session_id="s", event_type="product_view"))
        await s.commit()
        return user.id


async def test_declared_profile_alone_is_enough_to_trigger(db):
    """THE regression test. A user who set a target role and uploaded a resume
    was getting an empty recommendations page forever, because the cold-start
    floor only counted browsing events."""
    from app.agent.triggers import should_run

    user_id = await _make_user(declared=True, events=0)
    run, reason, _ = await should_run(user_id, "profile_change")
    assert run is True
    assert reason == "first_run"


async def test_a_blank_account_is_still_suppressed(db):
    """No declared profile and no behavior means a generated set would be a
    popularity list — the exact failure this project exists to avoid."""
    from app.agent.triggers import should_run

    user_id = await _make_user(declared=False, events=0)
    run, reason, _ = await should_run(user_id, "")
    assert run is False
    assert reason == "cold_start_floor"


async def test_debounce_suppresses_a_rapid_second_run(db):
    """Someone editing five profile fields must produce one run, not five."""
    from app.agent.triggers import should_run

    user_id = await _make_user(declared=True)
    async with async_session() as s:
        s.add(Recommendation(user_id=user_id, narrative="n", items=[],
                             fingerprint="f", is_current=True))
        await s.commit()

    run, reason, _ = await should_run(user_id, "profile_change")
    assert run is False
    assert reason == "debounce"


async def test_profile_change_beats_a_settled_set(db):
    """Past the debounce, a stated change must regenerate — that is what
    'recommendations evolve' means."""
    from app.agent.triggers import should_run

    user_id = await _make_user(declared=True)
    async with async_session() as s:
        s.add(Recommendation(
            user_id=user_id, narrative="n", items=[], fingerprint="f",
            is_current=True,
            created_at=datetime.utcnow() - timedelta(hours=1)))
        await s.commit()

    run, reason, _ = await should_run(user_id, "profile_change")
    assert run is True
    assert reason == "profile_change"


async def test_an_ats_run_after_the_current_set_retriggers(db):
    from app.agent.triggers import should_run

    user_id = await _make_user(declared=True)
    async with async_session() as s:
        s.add(Recommendation(
            user_id=user_id, narrative="n", items=[], fingerprint="f",
            is_current=True,
            created_at=datetime.utcnow() - timedelta(hours=2)))
        s.add(ResumeAnalysis(user_id=user_id, target_role="X", ats_score=60,
                             is_current=True, missing_skills=["rag"]))
        await s.commit()

    run, reason, _ = await should_run(user_id, "")
    assert run is True
    assert reason == "ats_run"


async def test_the_pipeline_is_never_fired_by_a_test(db):
    """`maybe_generate` is called from request handlers as a background task;
    one outliving its test hits the DB during teardown."""
    from app.agent.graph import maybe_generate

    result = await maybe_generate(await _make_user(declared=True), "profile_change")
    assert result["ran"] is False
    assert result["reason"] == "test_env"


# --- ranking end to end -----------------------------------------------------

async def test_ranking_excludes_a_course_already_bought(db):
    """The most visible possible failure: recommending what they own."""
    bought = await _make_product(title="Already Bought")
    other = await _make_product(title="Something Else")
    user_id = await _make_user(declared=True)
    async with async_session() as s:
        s.add(Event(event_uuid=uuid.uuid4().hex, user_id=user_id,
                    session_id="s", event_type="conversion",
                    product_id=bought.id))
        await s.commit()

    ranked = await fusion_rank.run(user_id, [bought, other], {}, top_k=5)
    assert bought.id not in [row["product"].id for row in ranked]
    assert other.id in [row["product"].id for row in ranked]


async def test_ranking_caps_one_category(db):
    """Five near-identical cards from one category is not a recommendation."""
    products = [_P(id=i, title=f"C{i}", category="GenAI") for i in range(1, 7)]
    user_id = await _make_user(declared=True)
    ranked = await fusion_rank.run(user_id, products, {}, top_k=3)
    # The cap allows CATEGORY_CAP, then backfills rather than returning short.
    assert len(ranked) == 3
    assert [row["rank"] for row in ranked] == [0, 1, 2]


async def test_ranks_are_contiguous_from_zero(db):
    products = [_P(id=i, title=f"C{i}", category=f"Cat{i}") for i in range(1, 5)]
    ranked = await fusion_rank.run(await _make_user(declared=True), products,
                                   {}, top_k=4)
    assert [row["rank"] for row in ranked] == list(range(len(ranked)))


# --- template copy ----------------------------------------------------------

def test_template_copy_names_the_actual_gaps():
    """Regression: a course scoring gap_match=1.0 was getting the generic
    'a common route into…' hook, copy that contradicted its own score."""
    course = _P(title="RAG Bootcamp", tags=["rag", "langgraph"],
                description="retrieval augmented generation")
    ranked = [{"product": course, "rank": 0, "score": 0.8,
               "terms": {"gap_match": 1.0, "interest_match": 0.0}}]
    facts = {"missing_skills": ["rag", "langgraph"],
             "target_role": "GenAI Engineer"}
    _, items = generate._template_items(ranked, facts)
    assert "rag" in items[0]["hook"].lower()
    assert "common route" not in items[0]["hook"]


def test_template_copy_is_honest_about_cold_start():
    ranked = [{"product": _P(title="X"), "rank": 0, "score": 0.2, "terms": {}}]
    narrative, _ = generate._template_items(ranked, {"cold_start": True})
    assert "new here" in narrative.lower()
