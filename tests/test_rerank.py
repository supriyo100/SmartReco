"""Reranking, conversation context, buy-intent, and the learning path.

These four modules are what turned chat retrieval from "RRF order" into a
career advisor, and each has a property worth pinning down:

  rerank   — must actually reorder, and must degrade to fusion order on a
             signal-free query rather than to something arbitrary
  context  — must survive a long thread without growing the prompt, and must
             not mangle what the user said about themselves
  intent   — must not pitch at people who objected or cannot afford it
  pathway  — must only draw edges the catalog states
"""
from __future__ import annotations

import re

import pytest

from app.chat.context import compact, extract_facts, retrieval_query, split_horizons
from app.chat.intent import score_intent
from app.chat.pathway import (
    _gain,
    _is_capability,
    _label,
    _stage_order,
    build_pathway_async,
)
from app.chat.rerank import cross_encode_score, pair_features, rerank


class _P:
    """Product stand-in. These modules touch only these fields."""
    def __init__(self, id, title, category="GenAI", level="intermediate",
                 price=5000.0, tags=None, description="", slug=None,
                 prereq_ids=None):
        self.id = id
        self.title = title
        self.category = category
        self.level = level
        self.price = price
        self.tags = tags or []
        self.description = description
        self.slug = slug or f"course-{id}"
        self.prereq_ids = prereq_ids or []


RAG = _P(1, "Ultimate RAG Bootcamp", tags=["rag", "vector database", "langchain"],
         description="Retrieval augmented generation, chunking, reranking.",
         slug="rag-bootcamp")
MLOPS = _P(2, "LLMOps Industry Ready Projects", category="MLOps",
           tags=["mlops", "kubernetes", "docker"],
           description="Deploy and monitor LLM systems in production.",
           slug="llmops")
DS = _P(3, "Ultimate Data Science Bootcamp", category="Data Science",
        level="beginner", price=10000.0, tags=["python", "statistics"],
        description="Pandas, statistics and machine learning foundations.",
        slug="ds-bootcamp")


# --- cross-encoder ----------------------------------------------------------

def test_pair_features_reward_the_on_topic_course():
    on_topic = pair_features("how do I learn RAG and vector databases", RAG)
    off_topic = pair_features("how do I learn RAG and vector databases", DS)
    assert on_topic["overlap"] > off_topic["overlap"]
    assert on_topic["tag_hit"] > 0
    assert off_topic["tag_hit"] == 0


def test_title_terms_outscore_description_terms():
    """A term in the title says what the course IS; the same term in
    paragraph four of the description may be an aside."""
    titled = _P(9, "Kubernetes for MLOps", tags=[], description="")
    buried = _P(10, "Generic Bootcamp", tags=[],
                description="We briefly mention kubernetes somewhere here.")
    assert cross_encode_score("kubernetes", titled) > cross_encode_score("kubernetes", buried)


async def test_rerank_promotes_the_relevant_course():
    """The whole point: RRF put DS first, reranking must fix that."""
    ranked, mode = await rerank("RAG and vector databases", [DS, MLOPS, RAG],
                                top_k=3, mode="cross_encoder")
    assert mode == "cross_encoder"
    assert ranked[0].id == RAG.id


async def test_fusion_mode_is_a_passthrough():
    ranked, mode = await rerank("anything", [DS, MLOPS, RAG], top_k=2,
                                mode="fusion")
    assert mode == "fusion"
    assert [p.id for p in ranked] == [DS.id, MLOPS.id]


async def test_signal_free_query_keeps_fusion_order():
    """A query with no usable terms must degrade to the input order, not to
    an arbitrary one — otherwise reranking makes results worse than not."""
    ranked, _ = await rerank("the and of", [DS, MLOPS, RAG], top_k=3,
                             mode="cross_encoder")
    assert [p.id for p in ranked] == [DS.id, MLOPS.id, RAG.id]


async def test_rerank_never_invents_or_drops_candidates():
    ranked, _ = await rerank("mlops kubernetes", [DS, MLOPS, RAG], top_k=3,
                             mode="cross_encoder")
    assert sorted(p.id for p in ranked) == sorted([DS.id, MLOPS.id, RAG.id])


# --- conversation context ---------------------------------------------------

def test_split_horizons_keeps_short_threads_verbatim():
    history = [{"role": "user", "content": f"m{i}"} for i in range(4)]
    older, recent = split_horizons(history)
    assert older == []
    assert recent == history


def test_split_horizons_caps_the_verbatim_window():
    history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    older, recent = split_horizons(history)
    assert len(recent) == 8
    assert len(older) == 22
    assert recent[-1]["content"] == "m29"


@pytest.mark.parametrize("text,expected", [
    ("my budget is around 7000", 7000.0),
    ("I can spend up to ₹5,000", 5000.0),
    ("budget is 8k", 8000.0),
    ("under 10000 please", 10000.0),
])
def test_budget_extraction(text, expected):
    assert extract_facts([{"role": "user", "content": text}])["budget_max"] == expected


def test_bare_numbers_are_not_budgets():
    """'3 years experience' must not become a ₹3 budget that filters the
    entire paid catalog out of every answer."""
    facts = extract_facts([{"role": "user", "content": "I have 3 years experience"}])
    assert facts["budget_max"] is None


@pytest.mark.parametrize("text,expected", [
    ("I want to become an AI engineer", "AI engineer"),
    ("my target role is senior data scientist", "senior data scientist"),
    ("I want to be a machine learning engineer", "machine learning engineer"),
])
def test_target_role_extraction(text, expected):
    assert extract_facts([{"role": "user", "content": text}])["target_role"] == expected


def test_a_sentence_is_not_a_job_title():
    """Regression: 'learn RAG and get into GenAI engineering' once captured
    the whole clause as the user's target role."""
    facts = extract_facts([{"role": "user",
                            "content": "I want to learn RAG and get into GenAI engineering"}])
    assert facts["target_role"] == ""


def test_only_user_turns_are_mined():
    """The assistant quotes prices constantly; treating that as a user
    statement would let our own suggestion become their stated budget."""
    facts = extract_facts([{"role": "assistant",
                            "content": "This one costs ₹9,000 — budget friendly."}])
    assert facts["budget_max"] is None


def test_compaction_preserves_the_numbers():
    older = [
        {"role": "user", "content": "I want to become an AI engineer"},
        {"role": "user", "content": "my budget is around 7000"},
        {"role": "assistant", "content": "Try **RAG** [[id:1]]"},
    ]
    text = compact(older)
    assert "7,000" in text          # the number survives, not "mentioned budget"
    assert "AI engineer" in text
    assert "1" in text              # course already discussed


def test_compaction_of_nothing_is_empty():
    assert compact([]) == ""
    assert compact([{"role": "user", "content": "hello"}]) == ""


def test_short_followup_borrows_the_previous_turn():
    """'what about the cheaper one?' has no retrievable noun on its own."""
    recent = [{"role": "user", "content": "tell me about RAG and langgraph"},
              {"role": "assistant", "content": "Here you go."}]
    query = retrieval_query("what about the cheaper one?", recent, "AI Engineer")
    assert "RAG" in query or "langgraph" in query
    assert "AI Engineer" in query


def test_long_message_is_not_diluted_with_history():
    recent = [{"role": "user", "content": "something completely unrelated"}]
    long_message = ("I have been working with kubernetes and docker for three "
                    "years and want to move into deploying language models")
    query = retrieval_query(long_message, recent, "")
    assert "unrelated" not in query


# --- buy intent -------------------------------------------------------------

def test_enrolment_question_is_hot():
    result = score_intent("how do I enrol in this one?", [], [RAG])
    assert result["level"] == "hot"
    assert result["product_id"] == RAG.id


def test_ordinary_question_is_cold():
    result = score_intent("what is RAG?", [], [RAG])
    assert result["level"] == "cold"
    assert result["reasons"] == []


@pytest.mark.parametrize("objection", [
    "that's too expensive for me",
    "just looking around for now",
    "no thanks, not for me",
])
def test_objections_kill_the_pitch(objection):
    assert score_intent(objection, [], [RAG])["level"] == "cold"


def test_never_pitch_what_they_cannot_afford():
    """Enthusiasm plus an over-budget catalog is still not an offer."""
    expensive = _P(4, "Advanced Production AI", price=13000.0)
    result = score_intent("how do I enrol?", [], [expensive], {"budget_max": 7000})
    assert result["level"] == "cold"
    assert "all candidates over budget" in result["reasons"]


def test_no_course_means_no_offer():
    """A pitch with nothing to point at is a banner ad."""
    assert score_intent("I'm ready, sign me up", [], [])["level"] == "cold"


def test_bare_yes_needs_something_to_agree_with():
    suggested = [{"role": "assistant", "content": "Try **RAG** [[id:1]]"}]
    assert score_intent("yes", suggested, [RAG])["score"] > 0
    # Nothing was suggested, so "yes" is not agreement to buy.
    assert score_intent("yes", [], [RAG])["score"] == 0


def test_intent_score_is_bounded():
    result = score_intent("how do I enrol, sign me up, I'm ready, discount?",
                          [], [RAG])
    assert 0.0 <= result["score"] <= 1.0


def test_reasons_are_human_readable():
    """An explanation nobody can read is not an explanation — these end up in
    an admin lead view."""
    reasons = score_intent("how do I enrol?", [], [RAG])["reasons"]
    assert reasons
    for reason in reasons:
        assert "?:" not in reason and "\\b" not in reason


# --- learning path ----------------------------------------------------------

def test_stage_order_is_by_level_then_price():
    ordered = _stage_order([RAG, DS, MLOPS])
    assert ordered[0].id == DS.id            # the only beginner


def test_label_marks_truncation():
    label = _label("Ultimate RAG Bootcamp: Building Traditional to Agentic "
                   "Systems with Cloud Deployment")
    assert label.endswith("…")
    assert "<br/>" in label


def test_label_leaves_short_titles_alone():
    assert _label("LLMOps Projects") == "LLMOps Projects"


async def test_pathway_needs_more_than_one_course(db):
    """One course with no prerequisites is a suggestion, not a path."""
    assert await build_pathway_async([RAG], goal="AI Engineer") is None


async def test_pathway_orders_and_labels(db):
    pathway = await build_pathway_async([RAG, DS, MLOPS], goal="AI Engineer")
    assert pathway is not None
    assert pathway["mermaid"].startswith("flowchart LR")
    assert "AI Engineer" in pathway["mermaid"]
    # Beginner first — the path has to read as a path.
    assert pathway["steps"][0]["id"] == DS.id
    # Every step is a real course we were given, never an invented one.
    assert {s["id"] for s in pathway["steps"]} <= {RAG.id, DS.id, MLOPS.id}


def test_capability_split_prefers_skills_over_product_names():
    """The gain line answers "what can I do", not "what is installed".

    An earlier cut used a blocklist of vendor names and missed FAISS, Tavily,
    ChromaDB and Gemini on the real catalog, so every arrow read as a tooling
    inventory. The rule is structural now: capabilities are phrases, products
    are names.
    """
    for product in ("FAISS", "Tavily", "ChromaDB", "Gemini", "LangGraph",
                    "AWS Lambda", "Phi Data", "Google ADK"):
        assert not _is_capability(product), product
    for skill in ("Multi-agent orchestration and handoffs",
                  "Agent memory systems", "Context engineering",
                  "Prompt injection defence",
                  "Advanced RAG (hybrid search, HyDE, re-ranking)"):
        assert _is_capability(skill), skill


def test_gain_leads_with_capabilities_when_both_are_present():
    """Products may appear, but never before a capability."""
    mixed = _P(11, "Mixed", tags=["FAISS", "ChromaDB", "Agent memory systems",
                                  "Context engineering"])
    named = [s.strip() for s in _gain(mixed, None).split(",")]
    capabilities = {"Context engineering", "Agent memory systems"}
    # Every capability is named before the first product, whatever the cut.
    products = [i for i, s in enumerate(named) if s not in capabilities]
    caps = [i for i, s in enumerate(named) if s in capabilities]
    assert caps and (not products or max(caps) < min(products))


def test_gain_reports_the_delta_not_a_repeat():
    """Two steps both claiming the same skill is the dishonesty to avoid."""
    first = _P(12, "First", tags=["Agent memory systems", "Context engineering"])
    second = _P(13, "Second", tags=["Agent memory systems", "LLM guardrails",
                                    "Prompt injection defence"])
    gain = _gain(second, first)
    assert "Agent memory systems" not in gain
    assert "guardrails" in gain.lower() or "injection" in gain.lower()


def test_gain_falls_back_rather_than_going_blank():
    """A step whose every skill was covered still says something true."""
    first = _P(14, "First", tags=["Agent memory systems"])
    second = _P(15, "Second", tags=["Agent memory systems"])
    assert _gain(second, first) == "Agent memory systems"


async def test_pathway_terminals_are_not_the_same_label(db):
    """The shipped diagram read "Senior AI Architect → … → Senior AI Architect".

    Both terminals were built from `goal`, so the path drew a loop that told
    the user they end where they started.
    """
    pathway = await build_pathway_async([RAG, DS, MLOPS], goal="AI Engineer")
    assert pathway["mermaid"].count("AI Engineer") == 1
    assert "Where you are today" in pathway["mermaid"]
    assert "Ready for: AI Engineer" in pathway["mermaid"]


async def test_pathway_start_names_their_current_role(db):
    """A known current role turns the diagram into a before-and-after."""
    pathway = await build_pathway_async([RAG, DS, MLOPS], goal="AI Engineer",
                                        start_from="Data Analyst")
    assert "Today: Data Analyst" in pathway["mermaid"]
    assert "Ready for: AI Engineer" in pathway["mermaid"]


async def test_pathway_final_edge_is_explained(db):
    """The arrow into the goal is the one the reader cares most about."""
    pathway = await build_pathway_async([RAG, DS, MLOPS], goal="AI Engineer")
    final = pathway["mermaid"].strip().splitlines()
    assert any('-->|"' in line and "goal" in line for line in final)


async def test_pathway_edges_carry_the_gain(db):
    """Arrows say what each step adds — that is what makes it a path."""
    pathway = await build_pathway_async([RAG, DS, MLOPS], goal="AI Engineer")
    assert "-->|" in pathway["mermaid"]
    assert all(s["gain"] for s in pathway["steps"])


async def test_pathway_edge_labels_stay_short_and_safe(db):
    """A long or pipe-bearing edge label stretches or breaks the diagram."""
    wordy = _P(16, "Wordy", slug="wordy",
               tags=["Multi-agent orchestration and handoffs across teams",
                     "Context engineering | with pipes"])
    pathway = await build_pathway_async([wordy, RAG], goal="AI Engineer")
    for label in re.findall(r'-->\|"([^"]*)"\|', pathway["mermaid"]):
        assert len(label) <= 39, label
        assert "|" not in label


async def test_pathway_labels_cannot_break_mermaid(db):
    """A quote or newline in a title silently breaks the diagram in-browser."""
    nasty = _P(5, 'A "quoted" title\nwith a newline', slug="nasty")
    pathway = await build_pathway_async([nasty, RAG], goal='goal "x"')
    assert pathway is not None
    body = pathway["mermaid"].split("flowchart LR", 1)[1]
    # Only the quotes that delimit node labels remain, never quotes from data.
    assert '"quoted"' not in body
    assert "\n" not in body.replace("\n  ", "")
