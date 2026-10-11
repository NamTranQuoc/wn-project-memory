"""L1 · project overview: what the project is, its notes and policies, its sources."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.models.enums import SourceType, pg_enum


class Project(Base):
    __tablename__ = "l1_projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    overview: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Note(Base):
    __tablename__ = "l1_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_projects.id", ondelete="CASCADE"), nullable=False
    )
    note_key: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_policy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    # Set on every policy: when and through which channel the user approved this text
    # (mcp_elicitation | rest). A policy never exists without it.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_via: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_l1_notes_project_key", "project_id", "note_key", unique=True),
        Index("ix_l1_notes_project_policy", "project_id", "is_policy", "priority"),
        CheckConstraint(
            "NOT is_policy OR approved_at IS NOT NULL", name="ck_l1_notes_policy_approved"
        ),
    )


class Source(Base):
    __tablename__ = "l1_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_projects.id", ondelete="CASCADE"), nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        pg_enum(SourceType, "source_type_enum"), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    # The place a human opens: chat link, Jira project, repo URL, folder path.
    url: Mapped[str | None] = mapped_column(Text)
    connection_config: Mapped[dict | None] = mapped_column(JSONB)
    read_recipe: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (Index("uq_l1_sources_project_key", "project_id", "source_key", unique=True),)
