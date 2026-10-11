.PHONY: sync run migrate db-up db-down test test-db build privacy-check hooks embed-backfill diagrams

sync:
	uv sync

run:
	uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000

migrate:
	uv run alembic upgrade head

db-up:
	docker compose up -d

db-down:
	docker compose down

test:
	uv run pytest -q

# Integration tests against a throwaway database: make test-db TEST_DATABASE_URL=postgresql+asyncpg://…
test-db:
	DATABASE_URL_SYNC=$(subst +asyncpg,+psycopg,$(TEST_DATABASE_URL)) uv run alembic upgrade head
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) uv run pytest -q

build:
	docker build -t agentic-memory:dev .

privacy-check:
	python3 scripts/privacy_check.py

hooks:
	git config core.hooksPath .githooks

embed-backfill:
	uv run python -m scripts.embed_backfill

diagrams:
	./docs/diagrams/build.sh
