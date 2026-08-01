.PHONY: sync run mcp migrate db-up db-down build test

sync:
	uv sync

run:
	uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

mcp:
	uv run python -m src.mcp_server

migrate:
	uv run alembic upgrade head

db-up:
	docker compose up -d

db-down:
	docker compose down

build:
	docker build -t memory-agent .

test:
	uv run pytest -v
