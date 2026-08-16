import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.config import get_settings
from src.core.db import Base

settings = get_settings()


class SourceType(str, enum.Enum):
    github_pr = "github_pr"
    github_repo = "github_repo"
    teams_chat = "teams_chat"
    teams_dm = "teams_dm"
    jira = "jira"
    local_file = "local_file"
    user_session = "user_session"
    other = "other"


class SourceAddedVia(str, enum.Enum):
    init = "init"
    manual = "manual"


class FactKind(str, enum.Enum):
    fact = "fact"
    decision = "decision"
    plan = "plan"
    question = "question"
    issue = "issue"
    solution = "solution"


class TaskStatus(str, enum.Enum):
    open = "open"
    partial = "partial"
    closed = "closed"


class WatchedRefType(str, enum.Enum):
    pr = "pr"
    issue = "issue"
    sha = "sha"
    path = "path"
    ticket = "ticket"
    tag = "tag"
    other = "other"


class WatchedRefDisposition(str, enum.Enum):
    mine = "mine"
    queued = "queued"
    resolved = "resolved"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_path", name="uq_projects_project_path"),
        Index("ix_projects_project_path", "project_path"),
    )


class DataSource(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_key: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            name="source_type_enum",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    connection_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    read_recipe: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_via: Mapped[SourceAddedVia] = mapped_column(
        Enum(
            SourceAddedVia,
            name="source_added_via_enum",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SourceAddedVia.manual,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "source_key", name="uq_sources_project_key"),
        Index("ix_sources_project_id", "project_id"),
        Index("ix_sources_project_path", "project_path"),
        Index("ix_sources_source_key", "source_key"),
    )


class L0WorkingMemory(Base):
    __tablename__ = "l0_working_memory"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
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
        UniqueConstraint("project_path", name="uq_l0_project_path"),
        Index("ix_l0_project_path", "project_path"),
        Index("ix_l0_working_memory_project_id", "project_id"),
    )


class L1Reference(Base):
    __tablename__ = "l1_references"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    ref_key: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_policy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    # No FK to partitioned l4_raw_events (Postgres limitation); indexed UUID only.
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "ref_key", name="uq_l1_references_project_ref_key"),
        Index("ix_l1_references_project_path", "project_path"),
        Index("ix_l1_references_project_id", "project_id"),
        Index("ix_l1_references_ref_key", "ref_key"),
        Index("ix_l1_references_project_is_policy", "project_id", "is_policy"),
        Index("ix_l1_references_source_id", "source_id"),
        Index(
            "ix_l1_references_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
        Index(
            "ix_l1_references_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class L2MetaMemory(Base):
    __tablename__ = "l2_meta_memory"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    environment_setup: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_structure: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_path", name="uq_l2_project_path"),
        Index("ix_l2_project_path", "project_path"),
        Index("ix_l2_project_id", "project_id"),
    )


class L3DistilledKnowledge(Base):
    __tablename__ = "l3_distilled_knowledge"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # No FK to partitioned l4_raw_events (Postgres limitation); indexed UUID only.
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    embedding = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    last_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_path", "entity_key", name="uq_l3_project_entity"),
        Index("ix_l3_project_path", "project_path"),
        Index("ix_l3_project_id", "project_id"),
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
        Index("ix_l4_project_id", "project_id"),
        Index("ix_l4_source_id", "source_id"),
        {
            "postgresql_partition_by": "RANGE (created_at)",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        primary_key=True,
    )


class L3SourceUnit(Base):
    """Durable per-unit ledger for incremental source ingest (survives L4 retention)."""

    __tablename__ = "l3_source_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    stream_key: Mapped[str] = mapped_column(
        String(256), nullable=False, server_default=text("''"), default=""
    )
    item_key: Mapped[str] = mapped_column(String(512), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # No FK to partitioned l4_raw_events (Postgres limitation); indexed UUID only.
    last_raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    unit_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_id",
            "stream_key",
            "item_key",
            name="uq_l3_source_units_project_source_stream_item",
        ),
        Index("ix_l3_source_units_project_id", "project_id"),
        Index("ix_l3_source_units_project_path", "project_path"),
        Index("ix_l3_source_units_source_id", "source_id"),
        Index(
            "ix_l3_source_units_source_stream",
            "project_id",
            "source_id",
            "stream_key",
        ),
        Index("ix_l3_source_units_item_key", "item_key"),
        Index("ix_l3_source_units_external_id", "external_id"),
        Index("ix_l3_source_units_content_hash", "content_hash"),
        Index("ix_l3_source_units_source_hash", "source_hash"),
        Index("ix_l3_source_units_last_raw_event_id", "last_raw_event_id"),
    )


class L3Watermark(Base):
    __tablename__ = "l3_watermarks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    # No FK to partitioned l4_raw_events.
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    l3_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_distilled_knowledge.id"), nullable=True
    )
    stream_key: Mapped[str] = mapped_column(
        String(256), nullable=False, server_default=text("''"), default=""
    )
    indexed_through: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    full_read_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    known_gaps: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_id",
            "stream_key",
            name="uq_l3_watermarks_project_source_stream",
        ),
        Index("ix_l3_watermarks_project_id", "project_id"),
        Index("ix_l3_watermarks_project_path", "project_path"),
        Index("ix_l3_watermarks_source_id", "source_id"),
        Index("ix_l3_watermarks_checked_at", "checked_at"),
    )


class L3Fact(Base):
    __tablename__ = "l3_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    l3_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_distilled_knowledge.id"), nullable=True
    )
    kind: Mapped[FactKind] = mapped_column(
        Enum(
            FactKind,
            name="fact_kind_enum",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=FactKind.fact,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_l3_facts_project_id", "project_id"),
        Index("ix_l3_facts_project_path", "project_path"),
        Index("ix_l3_facts_kind", "kind"),
        Index("ix_l3_facts_project_occurred", "project_id", "occurred_at"),
        Index("ix_l3_facts_project_priority", "project_id", "priority"),
        Index("ix_l3_facts_source_id", "source_id"),
        Index("ix_l3_facts_raw_event_id", "raw_event_id"),
        Index("ix_l3_facts_l3_entity_id", "l3_entity_id"),
        Index(
            "ix_l3_facts_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
        Index(
            "ix_l3_facts_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class L3Task(Base):
    __tablename__ = "l3_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    l3_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_distilled_knowledge.id"), nullable=True
    )
    task_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(
            TaskStatus,
            name="task_status_enum",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=TaskStatus.open,
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    waiting_on: Mapped[str | None] = mapped_column(String(512), nullable=True)
    since_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "task_key", name="uq_l3_tasks_project_key"),
        Index("ix_l3_tasks_project_id", "project_id"),
        Index("ix_l3_tasks_project_path", "project_path"),
        Index("ix_l3_tasks_status", "status"),
        Index("ix_l3_tasks_project_priority", "project_id", "priority"),
        Index("ix_l3_tasks_since_at", "since_at"),
        Index("ix_l3_tasks_source_id", "source_id"),
        Index(
            "ix_l3_tasks_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
        Index(
            "ix_l3_tasks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class L3WatchedRef(Base):
    __tablename__ = "l3_watched_refs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    l3_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_distilled_knowledge.id"), nullable=True
    )
    ref_type: Mapped[WatchedRefType] = mapped_column(
        Enum(
            WatchedRefType,
            name="watched_ref_type_enum",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    ref_value: Mapped[str] = mapped_column(String(1024), nullable=False)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition: Mapped[WatchedRefDisposition] = mapped_column(
        Enum(
            WatchedRefDisposition,
            name="watched_ref_disposition_enum",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=WatchedRefDisposition.queued,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "ref_type",
            "ref_value",
            name="uq_l3_watched_refs_project_type_value",
        ),
        Index("ix_l3_watched_refs_project_id", "project_id"),
        Index("ix_l3_watched_refs_project_path", "project_path"),
        Index("ix_l3_watched_refs_disposition", "disposition"),
        Index("ix_l3_watched_refs_source_id", "source_id"),
        Index(
            "ix_l3_watched_refs_why_trgm",
            "why",
            postgresql_using="gin",
            postgresql_ops={"why": "gin_trgm_ops"},
        ),
        Index(
            "ix_l3_watched_refs_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
