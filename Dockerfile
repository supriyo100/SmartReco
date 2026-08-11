# SmartReco — behavioral AI recommendation platform (FastAPI + LangGraph + Chroma)
# Single-process image: uvicorn --workers 1 is required (see README §10) because
# APScheduler runs in-process and two workers would drain the vector_outbox twice.

# ---- builder: compiles wheels only, never ships in the final image ----
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1
WORKDIR /app

# build-essential: chromadb's deps (hnswlib) build from source on slim images
# without a matching manylinux wheel. Confined to this stage.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- runtime: no compiler, no dev headers ----
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# Patch OS packages in the base image; no extra packages installed here —
# the healthcheck below uses the stdlib instead of pulling in curl.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY . .
RUN chmod +x docker-entrypoint.sh \
    # Runtime-generated dirs that are gitignored (empty/absent on a fresh
    # checkout); init_db and the mail fallback need them to exist first.
    && mkdir -p data/resumes data/outbox_mail chroma_data \
    # Drop root: bcrypt/pypdf/chromadb need no elevated privileges at runtime.
    && useradd --create-home --uid 1000 smartreco \
    && chown -R smartreco:smartreco /app

USER smartreco

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/', timeout=3)" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
