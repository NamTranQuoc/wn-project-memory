"""L3 · facts: one current value per key, with an ordered history of revisions."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.config import get_settings
from src.core.db import Base
from src.models.enums import ChangeType, FactKind, pg_enum


class Fact(Base):
    __tablename__ = "l3_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_projects.id", ondelete="CASCADE"), nullable=False
    )
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l2_namespaces.id"), nullable=False
    )
    fact_key: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[FactKind] = mapped_column(pg_enum(FactKind, "fact_kind_enum"), nullable=False)
    # task: open|partial|closed · watched_ref: mine|queued|resolved · others: free
    status: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # The line the L2 index shows.
    summary: Mapped[str] = mapped_column(String(280), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Kind-specific fields: waiting_on, ref_type, ref_value, …
    attrs: Mapped[dict | None] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL while semantic search is off.
    embedding = mapped_column(Vector(get_settings().embedding_dimensions), nullable=True)
    # Full-text search: key and title (A), summary (B), content (C); see services/text_search.py.
    search_tsv = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple'::regconfig, "
            "translate(fact_key || ' ' || title, './', '  ')), 'A') || "
            "setweight(to_tsvector('simple'::regconfig, translate(summary, './', '  ')), 'B') || "
            "setweight(to_tsvector('simple'::regconfig, translate(content, './', '  ')), 'C')",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_l3_facts_project_key", "project_id", "fact_key", unique=True),
        Index("ix_l3_facts_namespace_updated", "namespace_id", "updated_at"),
        Index("ix_l3_facts_search", "search_tsv", postgresql_using="gin"),
        # Typo-tolerant fallback on titles.
        Index(
            "ix_l3_facts_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_l3_facts_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Revision(Base):
    __tablename__ = "l3_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_facts.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[ChangeType] = mapped_column(
        pg_enum(ChangeType, "change_type_enum"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Snapshot of the full value at this revision.
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(String(280), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(String(32))
    attrs: Mapped[dict | None] = mapped_column(JSONB)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("uq_l3_revisions_fact_rev", "fact_id", "revision", unique=True),)


class RevisionSource(Base):
    __tablename__ = "l3_revision_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_revisions.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_sources.id"), nullable=False
    )
    # The real place: PR comment, Teams message, Jira issue.
    source_url: Mapped[str | None] = mapped_column(Text)
    # The L4 copy. It can expire, so no FK (Postgres cannot reference the partitioned table).
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_l3_revision_sources_revision", "revision_id"),
        Index("ix_l3_revision_sources_raw", "raw_event_id"),
        Index("ix_l3_revision_sources_source", "source_id"),
    )


class FactLink(Base):
    __tablename__ = "l3_links"

    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_facts.id", ondelete="CASCADE"), primary_key=True
    )
    related_fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l3_facts.id", ondelete="CASCADE"), primary_key=True
    )
    # 'replaces' | 'depends_on' | 'related'
    relation: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_l3_links_related", "related_fact_id"),)
