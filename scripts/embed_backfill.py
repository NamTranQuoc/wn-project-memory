"""Embed L3 facts that have no vector yet. Run after turning SEMANTIC_SEARCH_ENABLED on.

    uv run python -m scripts.embed_backfill [--batch 50]
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from src.core.db import AsyncSessionLocal, engine
from src.models import Fact, Project
from src.services.embedding import embed_text, semantic_enabled


async def backfill(batch: int) -> int:
    if not semantic_enabled():
        raise SystemExit("SEMANTIC_SEARCH_ENABLED is false; nothing to do")
    done = failed = 0
    while True:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(Fact, Project.project_path)
                    .join(Project, Project.id == Fact.project_id)
                    .where(Fact.embedding.is_(None))
                    .limit(batch)
                )
            ).all()
            if not rows:
                break
            for fact, project_path in rows:
                try:
                    fact.embedding = await embed_text(f"{fact.title}\n{fact.summary}\n{fact.content}")
                    done += 1
                except Exception as exc:  # noqa: BLE001 — report and keep going
                    failed += 1
                    print(f"failed {project_path} {fact.fact_key}: {exc}")
            await session.commit()
            if failed and failed >= done:
                break
    await engine.dispose()
    print(f"embedded {done} · failed {failed}")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=50)
    raise SystemExit(asyncio.run(backfill(parser.parse_args().batch)))


if __name__ == "__main__":
    main()
