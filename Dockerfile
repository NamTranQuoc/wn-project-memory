# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY memory_agent_skill.md CLAUDE.md README.md ./

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
