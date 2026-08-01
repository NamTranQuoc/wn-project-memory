from __future__ import annotations

import json
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import L4RawEvent
from src.models.memory import DistillationStatus
from src.services.embedding_service import embed_text
from src.services.hashing import compute_source_hash
from src.services.litellm_client import chat_completion
from src.services.memory_service import upsert_distilled_rule

logger = logging.getLogger(__name__)

DISTILLATION_SYSTEM_PROMPT = """You are a knowledge distillation engine.
Split the raw event into independent atomic facts/rules for a project memory system.
Return ONLY valid JSON of the form:
{"facts": [{"entity_key": "short_snake_case_key", "content": "one atomic fact"}]}
Rules:
- Each fact must be independently meaningful.
- entity_key must be unique within this batch and stable/descriptive.
- Do not invent facts not supported by the source text.
- If nothing distillable, return {"facts": []}.
"""


def _slugify_key(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:128] or "unnamed_fact"


async def _extract_facts(raw_content: str) -> list[dict[str, str]]:
    content = await chat_completion(
        [
            {"role": "system", "content": DISTILLATION_SYSTEM_PROMPT},
            {"role": "user", "content": raw_content},
        ],
        response_format={"type": "json_object"},
    )
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        logger.error("Distillation returned non-JSON: %s", content[:500])
        return []

    facts = payload.get("facts", [])
    normalized: list[dict[str, str]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        entity_key = _slugify_key(str(fact.get("entity_key", "")))
        fact_content = str(fact.get("content", "")).strip()
        if entity_key and fact_content:
            normalized.append({"entity_key": entity_key, "content": fact_content})
    return normalized


async def distill_event(session: AsyncSession, event: L4RawEvent) -> dict:
    try:
        facts = await _extract_facts(event.raw_content)
        results = []
        for fact in facts:
            embedding = await embed_text(fact["content"])
            result = await upsert_distilled_rule(
                session,
                project_path=event.project_path,
                entity_key=fact["entity_key"],
                content=fact["content"],
                raw_event_id=event.id,
                source_hash=event.source_hash or compute_source_hash(event.raw_content),
                embedding=embedding,
            )
            results.append(result)

        event.distillation_status = DistillationStatus.processed
        await session.flush()
        return {
            "raw_event_id": str(event.id),
            "status": "processed",
            "facts_count": len(results),
            "facts": results,
        }
    except Exception:
        logger.exception("Distillation failed for event %s", event.id)
        event.distillation_status = DistillationStatus.failed
        await session.flush()
        return {
            "raw_event_id": str(event.id),
            "status": "failed",
            "facts_count": 0,
            "facts": [],
        }


async def distill_event_by_id(
    session: AsyncSession, raw_event_id: uuid.UUID | str
) -> dict:
    event_id = (
        raw_event_id
        if isinstance(raw_event_id, uuid.UUID)
        else uuid.UUID(str(raw_event_id))
    )
    result = await session.execute(select(L4RawEvent).where(L4RawEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        return {"error": "not_found", "raw_event_id": str(event_id)}
    return await distill_event(session, event)


async def process_pending_events(
    session: AsyncSession, *, batch_size: int = 10
) -> list[dict]:
    """Sweep pending + failed L4 events and attempt distillation (retry via DB state)."""
    result = await session.execute(
        select(L4RawEvent)
        .where(
            L4RawEvent.distillation_status.in_(
                [DistillationStatus.pending, DistillationStatus.failed]
            )
        )
        .order_by(L4RawEvent.created_at.asc())
        .limit(batch_size)
    )
    events = list(result.scalars().all())
    outcomes: list[dict] = []
    for event in events:
        # Reset failed -> pending before retry attempt for observability
        if event.distillation_status == DistillationStatus.failed:
            event.distillation_status = DistillationStatus.pending
            await session.flush()
        outcomes.append(await distill_event(session, event))
    return outcomes
