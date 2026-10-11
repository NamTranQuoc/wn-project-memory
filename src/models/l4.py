"""L4 · raw events: append-only payloads, partitioned by the month of the last touch."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class RawEvent(Base):
    __tablename__ = "l4_raw_events"
    __table_args__ = (
        Index("ix_l4_raw_events_id", "id"),
        Index("ix_l4_raw_events_project_created", "project_path", "created_at"),
        Index("ix_l4_raw_events_source", "source_id"),
        {"postgresql_partition_by": "RANGE (last_touched_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_projects.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized so query_deep_memory_sql can scope rows without a join.
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_sources.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Partition key. A touch moves the row to the partition of the current month.
    last_touched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, primary_key=True
    )
