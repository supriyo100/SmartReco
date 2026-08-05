.PHONY: dev seed test lint init

init:
	python -m app.db.init_db

seed:
	python -m app.db.seed

dev:
	uvicorn app.main:app --reload --workers 1

test:
	python -m pytest tests/ -q

lint:
	ruff check app/ evals/ tests/
