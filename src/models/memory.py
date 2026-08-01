import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.config import get_settings
from src.core.db import Base

settings = get_settings()


class DistillationStatus(str, enum.Enum):
    pending = "pending"
    processed = "processed"
    failed = "failed"


class L1WorkingMemory(Base):
    __tablename__ = "l1_working_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    current_focus_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_path", name="uq_l1_project_path"),
        Index("ix_l1_project_path", "project_path"),
    )


class L2MetaMemory(Base):
    __tablename__ = "l2_meta_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    environment_setup: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_structure: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_path", name="uq_l2_project_path"),
        Index("ix_l2_project_path", "project_path"),
    )


class L3DistilledKnowledge(Base):
    __tablename__ = "l3_distilled_knowledge"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    embedding = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    last_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_path", "entity_key", name="uq_l3_project_entity"
        ),
        Index("ix_l3_project_path", "project_path"),
        Index("ix_l3_raw_event_id", "raw_event_id"),
        Index("ix_l3_content_hash", "content_hash"),
        Index("ix_l3_source_hash", "source_hash"),
        Index(
            "ix_l3_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
        Index(
            "ix_l3_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class L4RawEvent(Base):
    __tablename__ = "l4_raw_events"
    __table_args__ = (
        Index("ix_l4_project_created", "project_path", "created_at"),
        Index("ix_l4_distillation_status", "distillation_status"),
        {
            "postgresql_partition_by": "RANGE (created_at)",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        primary_key=True,
    )
    distillation_status: Mapped[DistillationStatus] = mapped_column(
        Enum(
            DistillationStatus,
            name="distillation_status_enum",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=text("'pending'"),
        default=DistillationStatus.pending,
    )
