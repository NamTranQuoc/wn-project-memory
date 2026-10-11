"""Shared plumbing for REST routes: one session per request, domain errors → HTTP codes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import AsyncSessionLocal
from src.services.common import DomainError, error_payload


async def run(work: Callable[[AsyncSession], Awaitable[dict]]) -> dict:
    async with AsyncSessionLocal() as session:
        try:
            result = await work(session)
            await session.commit()
            return result
        except (DomainError, ValueError) as exc:
            await session.rollback()
            status = exc.status if isinstance(exc, DomainError) else 422
            raise HTTPException(status, error_payload(exc)) from exc
