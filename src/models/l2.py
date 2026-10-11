"""L2 · key map: each namespace sets the key template for one group of L3 facts."""

import uuid
from datetime import datetime

from sqlalchemy import Computed, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.models.enums import FactKind, pg_enum


class Namespace(Base):
    __tablename__ = "l2_namespaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("l1_projects.id", ondelete="CASCADE"), nullable=False
    )
    # 'decision.api'
    namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    # 'decision.api.{service}.{topic}'
    key_template: Mapped[str] = mapped_column(String(512), nullable=False)
    default_kind: Mapped[FactKind] = mapped_column(
        pg_enum(FactKind, "fact_kind_enum"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"), default=list
    )
    # Full-text search over namespace and summary; see services/text_search.py.
    search_tsv = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple'::regconfig, translate(namespace, './', '  ')), 'A') || "
            "setweight(to_tsvector('simple'::regconfig, translate(summary, './', '  ')), 'B')",
            persisted=True,
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_l2_namespaces_project_ns", "project_id", "namespace", unique=True),
        Index("ix_l2_namespaces_keywords", "keywords", postgresql_using="gin"),
        Index("ix_l2_namespaces_search", "search_tsv", postgresql_using="gin"),
    )
