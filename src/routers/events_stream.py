from __future__ import annotations

import asyncio
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from src.core.db import get_session
from src.models import L4RawEvent

router = APIRouter(prefix="/projects", tags=["events-stream"])


@router.get("/{project_path:path}/events/{raw_event_id}/stream")
async def stream_event_status(
    project_path: str,
    raw_event_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    """SSE stream of distillation_status changes for a raw L4 event."""
    decoded_path = unquote(project_path)

    async def event_generator():
        last_status: str | None = None
        while True:
            if await request.is_disconnected():
                break

            result = await session.execute(
                select(L4RawEvent).where(
                    L4RawEvent.id == raw_event_id,
                    L4RawEvent.project_path == decoded_path,
                )
            )
            event = result.scalar_one_or_none()
            if event is None:
                yield {
                    "event": "error",
                    "data": '{"error":"not_found"}',
                }
                break

            status = event.distillation_status.value
            if status != last_status:
                last_status = status
                yield {
                    "event": "status",
                    "data": (
                        f'{{"raw_event_id":"{raw_event_id}",'
                        f'"distillation_status":"{status}"}}'
                    ),
                }
                if status in ("processed",):
                    break

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())
