.PHONY: dev seed test lint init validate catalogue ingest

init:
	python -m app.db.init_db

seed:
	python -m app.db.seed

# Run after EVERY curated course — structure (course.schema.json) + curation
# rules. Errors block ingest; warnings are prompts to confirm, not to silence.
validate:
	python data/data_1/validate_seed.py data/data_1

# One summary row per course -> data/courses_catalogue.json. The curation
# TRACKER (what is in the catalog, what is still wrong with it) — not to be
# confused with data/catalog.index.json, the runtime Tier-3 document lookup that
# ingest writes. `--check` fails when the tracker is stale, e.g. after a cohort
# date silently passes into history.
catalogue:
	python -m app.catalog.catalogue data/data_1

# Chunk -> embed (batched) -> Chroma, plus data/catalog.index.json for Tier-3
# generate-time injection. --dry-run needs no Mesh key.
ingest: validate catalogue
	python -m app.catalog.ingest data/data_1 --allow-pending

dev:
	uvicorn app.main:app --reload --workers 1

test:
	python -m pytest tests/ -q

lint:
	ruff check app/ evals/ tests/
