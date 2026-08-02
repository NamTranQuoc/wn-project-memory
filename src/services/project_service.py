from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Project


async def get_or_create_project(
    session: AsyncSession,
    project_path: str,
    *,
    display_name: str | None = None,
) -> Project:
    """Resolve project_path to a Project row, creating it if missing."""
    result = await session.execute(select(Project).where(Project.project_path == project_path))
    project = result.scalar_one_or_none()
    if project is not None:
        return project

    name = display_name or Path(project_path.rstrip("/")).name or project_path
    project = Project(
        project_path=project_path,
        display_name=name,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(project)
    await session.flush()
    return project
