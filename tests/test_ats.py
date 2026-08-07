"""ATS scoring — deterministic, so it is testable in the strongest sense.

The reason the scorer is pure Python rather than an LLM call (app/profiles/ats.py)
is exactly what these tests exercise: identical input must produce an identical
score, and a better resume must score higher than a worse one. Neither property
is available from a model asked to rate a document out of 100.
"""
import pytest

from app.profiles.ats import ROLES, _mentions, analyze, band, parse_resume, resolve_role

STRONG = """Priya Sharma
priya@example.com | +91 98765 43210 | linkedin.com/in/priyas | github.com/priyas

SUMMARY
Generative AI engineer building retrieval systems in production.

EXPERIENCE
Senior AI Engineer, Acme  Jan 2021 - Present
- Built a RAG pipeline over 2M documents with LangChain and Chroma, cutting
  support handling time by 35%
- Shipped LangGraph agents with tool calling; reduced escalations 3x
- Designed FastAPI services on AWS serving 12k requests/sec
- Mentored 4 engineers and led the migration to Docker and CI/CD
AI Engineer, Globex  Jun 2018 - Dec 2020
- Developed embeddings search over a vector database, improving recall 22%
- Automated evaluation harnesses for prompt engineering experiments
- Deployed models with MLflow and monitored drift in production

SKILLS
Python, LLM, RAG, LangChain, LangGraph, prompt engineering, embeddings,
vector database, OpenAI API, Git, Docker, AWS, evaluation, fine-tuning

EDUCATION
B.Tech Computer Science, 2018

PROJECTS
Open-source agent framework with 1.2k GitHub stars
"""

WEAK = """John
Worked on some projects.
Responsible for various tasks.
Familiar with computers.
"""


def test_identical_input_scores_identically():
    """The property an LLM scorer cannot offer."""
    assert analyze(STRONG, "Generative AI Engineer") == analyze(STRONG, "Generative AI Engineer")


def test_strong_resume_outscores_weak_one():
    strong = analyze(STRONG, "Generative AI Engineer")["ats_score"]
    weak = analyze(WEAK, "Generative AI Engineer")["ats_score"]
    assert strong > weak
    assert strong >= 65 and weak <= 40


def test_score_and_subscores_stay_in_range():
    for text in (STRONG, WEAK, "", "x"):
        r = analyze(text, "Data Scientist")
        for key in ("ats_score", "keyword_score", "structure_score",
                    "experience_score", "readability_score"):
            assert 0 <= r[key] <= 100, (key, r[key])


def test_empty_resume_returns_zero_and_explains_rather_than_raising():
    r = analyze("", "AI / ML Engineer")
    assert r["ats_score"] == 0
    assert r["warnings"] and "upload" in r["warnings"][0].lower()
    # Still names the gaps, so a user with no resume gets direction anyway.
    assert r["missing_skills"]


def test_same_resume_scores_differently_against_different_roles():
    """Why the (resume, role) pair is the key, not the resume alone."""
    genai = analyze(STRONG, "Generative AI Engineer")
    data_eng = analyze(STRONG, "Data Engineer")
    assert genai["ats_score"] != data_eng["ats_score"]
    assert genai["keyword_score"] > data_eng["keyword_score"]


def test_missing_skills_are_actually_absent_from_the_text():
    """A false gap sends someone to buy a course teaching what they know."""
    r = analyze(STRONG, "Generative AI Engineer")
    lc = STRONG.lower()
    for skill in r["missing_skills"]:
        assert not _mentions(lc, skill), f"{skill!r} reported missing but present"


def test_matched_and_missing_partition_the_role_skills():
    r = analyze(STRONG, "Agentic AI Engineer")
    spec = ROLES["Agentic AI Engineer"]
    assert sorted(r["matched_skills"] + r["missing_skills"]) == sorted(
        spec["must"] + spec["nice"])


def test_aliases_prevent_false_gaps():
    """'torch' must satisfy pytorch; 'k8s' must satisfy kubernetes."""
    assert _mentions("we use torch daily", "pytorch")
    assert _mentions("ran k8s in prod", "kubernetes")
    assert _mentions("built with sklearn", "scikit-learn")


def test_word_boundaries_prevent_false_matches():
    """Substring matching is what makes naive ATS clones embarrassing."""
    assert not _mentions("i am going to the store", "go")
    assert not _mentions("nothing relevant here", "r")


@pytest.mark.parametrize("typed,expected", [
    ("Generative AI Engineer", "Generative AI Engineer"),   # exact
    ("llm engineer", "Generative AI Engineer"),             # title alias
    ("", "Generative AI Engineer"),                         # default
    ("completely unknown job", "Generative AI Engineer"),   # fallback
    ("data scientist", "Data Scientist"),
])
def test_resolve_role_always_returns_a_known_role(typed, expected):
    assert resolve_role(typed) == expected
    assert resolve_role(typed) in ROLES


def test_parse_extracts_contact_and_structure():
    p = parse_resume(STRONG)
    assert p["emails"] == ["priya@example.com"]
    assert p["phones"]
    assert "linkedin.com/in/priyas" in p["linkedin"]
    assert "github.com/priyas" in p["github"]
    assert {"experience", "skills", "education"} <= set(p["sections"])
    assert p["metrics"] >= 3          # 35%, 3x, 12k, 22%
    assert p["date_ranges"] >= 2
    assert p["name_guess"] == "Priya Sharma"


def test_weak_phrases_are_detected_and_surfaced():
    r = analyze(WEAK, "Software Engineer")
    assert r["parsed"]["weak_phrases"]
    assert any("passive" in w.lower() for w in r["warnings"])


def test_suggestions_are_actionable_and_bounded():
    r = analyze(WEAK, "AI / ML Engineer")
    assert 0 < len(r["suggestions"]) <= 6


def test_band_thresholds_are_ordered():
    assert band(95)[0] == "Strong"
    assert band(70)[0] == "Competitive"
    assert band(50)[0] == "Needs work"
    assert band(10)[0] == "At risk"
