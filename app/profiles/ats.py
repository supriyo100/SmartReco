"""ATS analysis: parse a resume, score it against a target role, name the gaps.

This is deterministic Python, and that is a design decision rather than a
shortcut. The architecture already argues (§6) that confidence is computed and
never asked of a model, for the same reason that applies with more force here:
an LLM asked "score this resume out of 100" returns a plausible number that
changes between runs on identical input. A user who edits their resume and
re-runs must be able to trust that a score which moved from 61 to 74 moved
because the resume improved, not because the model felt different. Reproducible
scoring is the whole value of the feature.

The output that actually matters is not the score — it is `missing_skills`.
That list is what connects this module to the rest of the system: the gaps
become retrieval queries, and the courses that come back are the ones that
close them. A score with no gap list would be a vanity metric.

Scoring is four weighted sub-scores (see `SCORE_WEIGHTS`), each of which
answers a question a real ATS pipeline asks:

  keyword     — does the resume contain the skills the target role screens for?
  structure   — can a parser find the sections and contact details it needs?
  experience  — is there evidence of doing the work (dates, metrics, verbs)?
  readability — is it the right length, and free of things that break parsers?
"""
from __future__ import annotations

import re
from collections import OrderedDict

# --- role definitions -------------------------------------------------------
# Hand-written rather than derived from the catalog. The catalog says what we
# teach; this says what the market screens for, and conflating the two would
# make the ATS score a measure of our own course coverage.
#
# `must` skills are weighted double `nice` ones: missing a core requirement is
# not the same size of gap as missing a bonus.

ROLES: "OrderedDict[str, dict]" = OrderedDict([
    ("AI / ML Engineer", {
        "must": ["python", "machine learning", "pytorch", "tensorflow", "sql",
                 "deep learning", "pandas", "numpy", "scikit-learn", "git"],
        "nice": ["mlops", "docker", "kubernetes", "aws", "spark", "airflow",
                 "mlflow", "feature engineering", "model deployment", "ci/cd"],
        "titles": ["ml engineer", "machine learning engineer", "ai engineer",
                   "data scientist"],
    }),
    ("Generative AI Engineer", {
        "must": ["python", "llm", "rag", "langchain", "prompt engineering",
                 "vector database", "embeddings", "openai", "api", "git"],
        "nice": ["langgraph", "fine-tuning", "huggingface", "chromadb", "pinecone",
                 "llamaindex", "agents", "docker", "aws", "evaluation"],
        "titles": ["genai engineer", "generative ai engineer", "llm engineer",
                   "ai engineer"],
    }),
    ("Agentic AI Engineer", {
        "must": ["python", "llm", "agents", "langgraph", "langchain", "rag",
                 "tool calling", "api", "vector database", "git"],
        "nice": ["multi-agent", "crewai", "autogen", "observability", "langsmith",
                 "docker", "aws", "evaluation", "mcp", "orchestration"],
        "titles": ["agentic ai engineer", "ai engineer", "llm engineer"],
    }),
    ("Senior AI Architect", {
        "must": ["system design", "architecture", "python", "llm", "cloud",
                 "scalability", "aws", "microservices", "api", "leadership"],
        "nice": ["kubernetes", "terraform", "cost optimization", "security",
                 "gcp", "azure", "mlops", "distributed systems", "mentoring",
                 "stakeholder management"],
        "titles": ["ai architect", "solutions architect", "principal engineer",
                   "staff engineer", "tech lead"],
    }),
    ("Data Scientist", {
        "must": ["python", "sql", "statistics", "pandas", "machine learning",
                 "data analysis", "visualization", "numpy", "experimentation",
                 "scikit-learn"],
        "nice": ["r", "tableau", "power bi", "a/b testing", "spark", "hypothesis testing",
                 "regression", "clustering", "storytelling", "excel"],
        "titles": ["data scientist", "data analyst", "research scientist"],
    }),
    ("Data Engineer", {
        "must": ["python", "sql", "etl", "data pipeline", "spark", "airflow",
                 "data warehouse", "cloud", "git", "modeling"],
        "nice": ["dbt", "kafka", "snowflake", "databricks", "aws", "gcp",
                 "terraform", "docker", "streaming", "bigquery"],
        "titles": ["data engineer", "analytics engineer", "etl developer"],
    }),
    ("MLOps Engineer", {
        "must": ["python", "docker", "kubernetes", "ci/cd", "mlops", "monitoring",
                 "cloud", "git", "model deployment", "automation"],
        "nice": ["mlflow", "terraform", "aws", "gcp", "prometheus", "grafana",
                 "kubeflow", "airflow", "observability", "sre"],
        "titles": ["mlops engineer", "ml platform engineer", "devops engineer"],
    }),
    ("Software Engineer", {
        "must": ["python", "git", "api", "sql", "testing", "algorithms",
                 "data structures", "debugging", "code review", "oop"],
        "nice": ["docker", "aws", "javascript", "react", "microservices", "ci/cd",
                 "system design", "agile", "linux", "redis"],
        "titles": ["software engineer", "backend engineer", "full stack engineer",
                   "developer"],
    }),
])

DEFAULT_ROLE = "Generative AI Engineer"

# Aliases exist because a resume says "PyTorch", "torch" or "py-torch" and an
# ATS that only matches the canonical spelling reports a gap the candidate does
# not have. False gaps are worse than missed ones: they send the user to buy a
# course teaching what they already know.
ALIASES: dict[str, list[str]] = {
    "machine learning": ["ml", "machine-learning"],
    "deep learning": ["dl", "neural network", "neural networks", "deep-learning"],
    "scikit-learn": ["sklearn", "scikit learn"],
    "pytorch": ["torch"],
    "tensorflow": ["tf", "keras"],
    "llm": ["large language model", "large language models", "gpt", "llms"],
    "rag": ["retrieval augmented generation", "retrieval-augmented generation"],
    "vector database": ["vector db", "vectordb", "chroma", "chromadb", "pinecone",
                        "weaviate", "faiss", "qdrant", "milvus"],
    "embeddings": ["embedding", "sentence transformers", "word2vec"],
    "prompt engineering": ["prompting", "prompt design"],
    "ci/cd": ["cicd", "ci cd", "continuous integration", "continuous delivery",
              "github actions", "jenkins", "gitlab ci"],
    "aws": ["amazon web services", "ec2", "s3", "sagemaker", "lambda"],
    "gcp": ["google cloud", "vertex ai", "bigquery"],
    "azure": ["microsoft azure", "azure ml"],
    "kubernetes": ["k8s", "eks", "gke"],
    "data pipeline": ["data pipelines", "pipelines", "etl pipeline"],
    "etl": ["elt", "extract transform load"],
    "data warehouse": ["warehouse", "snowflake", "redshift", "bigquery"],
    "sql": ["postgresql", "postgres", "mysql", "sqlite", "t-sql", "plsql"],
    "api": ["rest api", "restful", "fastapi", "graphql", "grpc", "endpoints"],
    "system design": ["systems design", "architecture design"],
    "architecture": ["architected", "architect", "architectural"],
    "leadership": ["led", "leading", "managed", "mentored", "team lead", "mentoring"],
    "mentoring": ["mentored", "mentor", "coaching"],
    "scalability": ["scalable", "scale", "high availability", "throughput"],
    "microservices": ["micro-services", "service oriented"],
    "cloud": ["aws", "gcp", "azure", "cloud-native", "cloud native"],
    "statistics": ["statistical", "stats", "probability"],
    "visualization": ["matplotlib", "seaborn", "plotly", "dashboards", "tableau"],
    "experimentation": ["a/b test", "a/b testing", "ab testing", "experiment"],
    "model deployment": ["deployed model", "serving", "inference", "model serving"],
    "monitoring": ["observability", "logging", "alerting", "telemetry"],
    "mlops": ["ml ops", "ml-ops"],
    "agents": ["agent", "agentic", "multi-agent", "autonomous agents"],
    "tool calling": ["function calling", "tool use", "tools"],
    "fine-tuning": ["finetuning", "fine tuning", "lora", "peft", "sft"],
    "testing": ["unit test", "unit tests", "pytest", "test coverage", "tdd"],
    "oop": ["object oriented", "object-oriented"],
    "data structures": ["data structure"],
    "docker": ["containerization", "containers", "dockerfile"],
    "git": ["github", "gitlab", "version control", "bitbucket"],
    "spark": ["pyspark", "apache spark"],
    "airflow": ["apache airflow", "dags"],
    "pandas": ["dataframe", "dataframes"],
    "evaluation": ["evals", "eval", "benchmarking"],
    "stakeholder management": ["stakeholders", "cross-functional"],
    "cost optimization": ["cost reduction", "cost savings", "finops"],
    "distributed systems": ["distributed system", "distributed computing"],
}

# Section headings an ATS parser looks for. A resume missing "experience"
# entirely is not a formatting nitpick — many parsers bucket text by heading and
# drop what they cannot place.
SECTION_PATTERNS = {
    "contact": r"(?:^|\n)\s*(?:contact|personal\s+details?)\b",
    "summary": r"(?:^|\n)\s*(?:summary|profile|objective|about\s+me)\b",
    "experience": (r"(?:^|\n)\s*(?:experience|employment|work\s+history"
                   r"|professional\s+experience)\b"),
    "education": r"(?:^|\n)\s*(?:education|academic|qualifications?)\b",
    "skills": r"(?:^|\n)\s*(?:skills?|technical\s+skills?|technologies|competencies)\b",
    "projects": r"(?:^|\n)\s*(?:projects?|portfolio)\b",
    "certifications": r"(?:^|\n)\s*(?:certifications?|certificates?|licenses?)\b",
}

# Weak verbs are not "bad writing" — they are unverifiable. "Responsible for
# the pipeline" does not say whether it was built, maintained or merely owned.
STRONG_VERBS = [
    "built", "designed", "led", "shipped", "architected", "launched", "reduced",
    "increased", "improved", "automated", "scaled", "delivered", "implemented",
    "optimized", "migrated", "developed", "created", "drove", "owned",
    "established", "mentored", "deployed", "engineered", "streamlined",
]
WEAK_PHRASES = [
    "responsible for", "worked on", "helped with", "involved in",
    "participated in", "assisted with", "duties included", "familiar with",
]

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Deliberately permissive: international formats vary too much to validate, and
# a false "no phone number" warning is more annoying than a missed one.
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s,;)]+", re.I)
LINKEDIN_RE = re.compile(r"(?:linkedin\.com/(?:in|pub)/[\w%-]+)", re.I)
GITHUB_RE = re.compile(r"(?:github\.com/[\w-]+)", re.I)
# Four-digit years, bounded so a "2400 requests/sec" metric is not read as a date.
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
DATE_RANGE_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}\b"
    r"|\b(?:19[89]\d|20[0-4]\d)\s*(?:-|–|—|to)\s*(?:present|current|now|(?:19[89]\d|20[0-4]\d))\b",
    re.I,
)
# A metric is a number that quantifies an outcome: a percentage, a scale
# suffix, a currency amount, or a multiplier.
METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*[kmb]\b|[$₹€£]\s*\d|\b\d+(?:\.\d+)?x\b",
    re.I,
)
BULLET_RE = re.compile(r"(?:^|\n)\s*[-•*▪◦‣·]\s+")

SCORE_WEIGHTS = {
    "keyword": 0.45,      # what an ATS actually filters on
    "structure": 0.20,    # whether a parser can read it at all
    "experience": 0.25,   # evidence of having done the work
    "readability": 0.10,  # length and parser hazards
}


def _variants(skill: str) -> list[str]:
    return [skill] + ALIASES.get(skill, [])


def _mentions(text_lc: str, skill: str) -> bool:
    """Word-boundary match on the skill or any alias.

    Substring matching is what makes naive ATS clones embarrassing: "R" matches
    every word containing it, and "go" matches "going". A boundary check costs
    one regex and removes the whole class. Non-word characters in the term
    (c++, ci/cd, scikit-learn) are escaped, and the boundary is only applied on
    the sides where it is meaningful.
    """
    for term in _variants(skill):
        term = term.strip().lower()
        if not term:
            continue
        left = r"\b" if term[0].isalnum() else ""
        right = r"\b" if term[-1].isalnum() else ""
        if re.search(f"{left}{re.escape(term)}{right}", text_lc):
            return True
    return False


def resolve_role(target: str) -> str:
    """Map free text to a known role, falling back rather than failing.

    The user types their target role; it will not always be one of ours. An
    exact match wins, then a title alias, then a token overlap, and only then
    the default. Returning a real role always is what lets the caller treat the
    score as meaningful without special-casing "unknown".
    """
    t = (target or "").strip().lower()
    if not t:
        return DEFAULT_ROLE
    for name in ROLES:
        if name.lower() == t:
            return name
    for name, spec in ROLES.items():
        if any(title in t for title in spec["titles"]):
            return name
    best, best_overlap = DEFAULT_ROLE, 0
    tokens = set(re.findall(r"[a-z]+", t))
    for name in ROLES:
        overlap = len(tokens & set(re.findall(r"[a-z]+", name.lower())))
        if overlap > best_overlap:
            best, best_overlap = name, overlap
    return best


def _first_match(pattern: "re.Pattern[str]", text: str) -> str:
    """The matched string, or "". One search, not three."""
    found = pattern.search(text or "")
    return found.group(0) if found else ""


def parse_resume(text: str) -> dict:
    """Structured extraction. Everything here is evidence, not judgement."""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    non_empty = [ln for ln in lines if ln]
    lc = (text or "").lower()

    emails = EMAIL_RE.findall(text or "")
    # A phone number is looked for in the header block only. Deeper in the
    # document, long digit runs are far more likely to be metrics or dates.
    header = "\n".join(non_empty[:12])
    phones = [p.strip() for p in PHONE_RE.findall(header)]

    sections = [key for key, pat in SECTION_PATTERNS.items()
                if re.search(pat, lc, re.I)]

    # The name heuristic: the first short line with no digits or '@'. It is a
    # guess, presented as one — it is never written back over a name the user
    # typed themselves.
    name_guess = ""
    for ln in non_empty[:5]:
        if 2 <= len(ln.split()) <= 5 and not any(c.isdigit() for c in ln) \
                and "@" not in ln and not URL_RE.search(ln):
            name_guess = ln[:120]
            break

    years = [int(y) for y in YEAR_RE.findall(text or "")]
    span = 0
    if years:
        # Span from earliest year mentioned to the latest, capped: it is an
        # upper bound on career length, not a measurement of it, and a stray
        # graduation year would otherwise inflate it.
        span = min(max(years) - min(years), 50)

    words = len((text or "").split())
    return {
        "name_guess": name_guess,
        "emails": emails[:3],
        "phones": phones[:2],
        "linkedin": _first_match(LINKEDIN_RE, text),
        "github": _first_match(GITHUB_RE, text),
        "sections": sections,
        "word_count": words,
        "line_count": len(non_empty),
        "bullet_count": len(BULLET_RE.findall(text or "")),
        "date_ranges": len(DATE_RANGE_RE.findall(text or "")),
        "metrics": len(METRIC_RE.findall(text or "")),
        "year_span": span,
        "strong_verbs": sorted({v for v in STRONG_VERBS
                                if re.search(rf"\b{v}\b", lc)}),
        "weak_phrases": sorted({w for w in WEAK_PHRASES if w in lc}),
    }


def _keyword_score(lc: str, spec: dict) -> tuple[int, list[str], list[str]]:
    """Coverage of role skills, `must` weighted double `nice`."""
    matched, missing = [], []
    got = total = 0.0
    for skill in spec["must"]:
        total += 2.0
        if _mentions(lc, skill):
            got += 2.0
            matched.append(skill)
        else:
            missing.append(skill)
    for skill in spec["nice"]:
        total += 1.0
        if _mentions(lc, skill):
            got += 1.0
            matched.append(skill)
        else:
            missing.append(skill)
    score = round(100 * got / total) if total else 0
    return score, matched, missing


def _structure_score(parsed: dict) -> tuple[int, list[str]]:
    """Can a parser find what it needs? Contact details and sections."""
    warnings: list[str] = []
    score = 100

    if not parsed["emails"]:
        score -= 25
        warnings.append("No email address found — most ATS parsers key the "
                        "candidate record on it.")
    if not parsed["phones"]:
        score -= 10
        warnings.append("No phone number found near the top of the resume.")

    for required in ("experience", "skills", "education"):
        if required not in parsed["sections"]:
            score -= 15
            warnings.append(f"No clearly labelled “{required}” section — parsers "
                            f"bucket text by heading and may drop what they "
                            f"cannot place.")
    if not parsed["linkedin"]:
        score -= 5
        warnings.append("No LinkedIn URL.")
    if parsed["bullet_count"] < 3:
        score -= 10
        warnings.append("Very few bullet points — dense paragraphs parse worse "
                        "and read slower than bulleted achievements.")
    return max(0, min(100, score)), warnings


def _experience_score(parsed: dict) -> tuple[int, list[str]]:
    """Evidence of doing the work: dated roles, quantified outcomes, strong verbs."""
    warnings: list[str] = []
    score = 0

    dates = parsed["date_ranges"]
    score += 30 if dates >= 2 else (18 if dates == 1 else 0)
    if dates == 0:
        warnings.append("No date ranges detected on any role — an ATS cannot "
                        "compute your years of experience without them.")

    metrics = parsed["metrics"]
    score += 30 if metrics >= 3 else (20 if metrics == 2 else (10 if metrics == 1 else 0))
    if metrics == 0:
        warnings.append("No quantified results (%, ₹/$, 10x, 200k). Numbers are "
                        "what turn a duty into an achievement.")

    verbs = len(parsed["strong_verbs"])
    score += 25 if verbs >= 5 else (15 if verbs >= 3 else (8 if verbs >= 1 else 0))

    weak = len(parsed["weak_phrases"])
    score += 15 if weak == 0 else (8 if weak == 1 else 0)
    if weak:
        warnings.append("Uses passive phrasing (" +
                        ", ".join(f"“{w}”" for w in parsed["weak_phrases"][:3]) +
                        ") — lead with what you did instead.")
    return max(0, min(100, score)), warnings


def _readability_score(parsed: dict) -> tuple[int, list[str]]:
    """Length and the things that quietly break parsers."""
    warnings: list[str] = []
    words = parsed["word_count"]
    if words < 150:
        score = 40
        warnings.append(f"Only ~{words} words — too thin to evidence a career. "
                        f"400–800 is the usual range.")
    elif words < 300:
        score = 70
        warnings.append(f"~{words} words is on the short side; 400–800 is typical.")
    elif words <= 900:
        score = 100
    elif words <= 1300:
        score = 80
        warnings.append(f"~{words} words is long — recruiters skim, and the "
                        f"first screen is often six seconds.")
    else:
        score = 55
        warnings.append(f"~{words} words is very long. Cut to the last 10 years "
                        f"and the roles relevant to your target.")
    return score, warnings


def _suggestions(role: str, missing: list[str], parsed: dict, sub: dict) -> list[str]:
    """Ordered by leverage: the cheapest fix with the largest score movement."""
    out: list[str] = []
    if missing:
        out.append("Add evidence of " + ", ".join(missing[:4]) +
                   f" — these are screened for in {role} roles and are absent "
                   f"from your resume.")
    if sub["experience"] < 60 and parsed["metrics"] == 0:
        out.append("Quantify two or three bullets. “Cut inference latency 40%” "
                   "outscores “improved performance” on every rubric.")
    if "summary" not in parsed["sections"]:
        out.append(f"Open with a two-line summary naming “{role}” — it is the "
                   f"first thing a human reads and it sets the keyword context.")
    if parsed["weak_phrases"]:
        out.append("Replace “" + parsed["weak_phrases"][0] + "” with a verb that "
                   "says what you actually did (built, shipped, led, reduced).")
    if not parsed["github"] and "projects" not in parsed["sections"]:
        out.append("Link a GitHub profile or add a projects section — for AI "
                   "roles, shipped code is the strongest available proof.")
    return out[:6]


def analyze(resume_text: str, target_role: str = "") -> dict:
    """Score a resume against a target role. Pure function, no I/O.

    Returns everything the ResumeAnalysis row needs. On empty input it returns
    a zeroed result with an explanatory warning rather than raising — the
    caller is a web form, and "no resume yet" is a normal state, not an error.
    """
    role = resolve_role(target_role)
    spec = ROLES[role]
    text = (resume_text or "").strip()

    if not text:
        return {
            "target_role": role, "ats_score": 0, "keyword_score": 0,
            "structure_score": 0, "experience_score": 0, "readability_score": 0,
            "matched_skills": [], "missing_skills": spec["must"][:],
            "parsed": {}, "resume_chars": 0,
            "warnings": ["No resume text to analyze — upload a file or paste "
                         "the text, then run the check again."],
            "suggestions": [],
        }

    lc = text.lower()
    parsed = parse_resume(text)
    kw, matched, missing = _keyword_score(lc, spec)
    st, st_warn = _structure_score(parsed)
    ex, ex_warn = _experience_score(parsed)
    rd, rd_warn = _readability_score(parsed)

    sub = {"keyword": kw, "structure": st, "experience": ex, "readability": rd}
    # Weighted-additive for the same reason confidence is (§6): multiplying
    # four [0,1] terms collapses a good resume toward zero, and one weak axis
    # would nuke an otherwise strong score.
    total = round(sum(sub[k] * w for k, w in SCORE_WEIGHTS.items()))

    return {
        "target_role": role,
        "ats_score": max(0, min(100, total)),
        "keyword_score": kw,
        "structure_score": st,
        "experience_score": ex,
        "readability_score": rd,
        "matched_skills": matched,
        "missing_skills": missing,
        "parsed": parsed,
        "warnings": st_warn + ex_warn + rd_warn,
        "suggestions": _suggestions(role, missing, parsed, sub),
        "resume_chars": len(text),
    }


def band(score: int) -> tuple[str, str]:
    """(label, css-class) for the score dial. Thresholds are deliberately
    conservative: real ATS filters commonly cut around 70–75%."""
    if score >= 80:
        return "Strong", "ats-strong"
    if score >= 65:
        return "Competitive", "ats-good"
    if score >= 45:
        return "Needs work", "ats-fair"
    return "At risk", "ats-poor"
