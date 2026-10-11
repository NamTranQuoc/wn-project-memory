"""Ingest ledger: per-unit dedup and per-stream cursors. Outlives L4 retention."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class IngestUnit(Base):
    __tablename__ = "ingest_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_sources.id", ondelete="CASCADE"), nullable=False
    )
    stream_key: Mapped[str] = mapped_column(
        String(256), nullable=False, server_default=text("''"), default=""
    )
    # 'ext:<external_id>' when the source has a stable id, else 'hash:<content_hash>'.
    item_key: Mapped[str] = mapped_column(String(512), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # May point to a raw event that retention already dropped.
    last_raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    unit_metadata: Mapped[dict | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_ingest_units_source_stream_item", "source_id", "stream_key", "item_key", unique=True
        ),
        Index("ix_ingest_units_source_stream_hash", "source_id", "stream_key", "source_hash"),
    )


class IngestWatermark(Base):
    __tablename__ = "ingest_watermarks"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_sources.id", ondelete="CASCADE"), primary_key=True
    )
    stream_key: Mapped[str] = mapped_column(
        String(256), primary_key=True, server_default=text("''"), default=""
    )
    # Advance only after the units up to this cursor were ingested and written.
    indexed_through: Mapped[dict | None] = mapped_column(JSONB)
    full_read_ids: Mapped[list | None] = mapped_column(JSONB)
    known_gaps: Mapped[list | None] = mapped_column(JSONB)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
