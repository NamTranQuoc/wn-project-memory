"""Add durable L3 source-unit ledger for incremental ingest dedup.

Revision ID: 006_source_unit_ledger
Revises: 005_switch_embedding_dim
Create Date: 2026-08-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "006_source_unit_ledger"
down_revision: str | None = "005_switch_embedding_dim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "l3_source_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("project_path", sa.String(1024), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("stream_key", sa.String(256), nullable=False, server_default=""),
        sa.Column("item_key", sa.String(512), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("last_raw_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unit_metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id",
            "source_id",
            "stream_key",
            "item_key",
            name="uq_l3_source_units_project_source_stream_item",
        ),
    )
    op.create_index("ix_l3_source_units_project_id", "l3_source_units", ["project_id"])
    op.create_index("ix_l3_source_units_project_path", "l3_source_units", ["project_path"])
    op.create_index("ix_l3_source_units_source_id", "l3_source_units", ["source_id"])
    op.create_index(
        "ix_l3_source_units_source_stream",
        "l3_source_units",
        ["project_id", "source_id", "stream_key"],
    )
    op.create_index("ix_l3_source_units_item_key", "l3_source_units", ["item_key"])
    op.create_index("ix_l3_source_units_external_id", "l3_source_units", ["external_id"])
    op.create_index("ix_l3_source_units_content_hash", "l3_source_units", ["content_hash"])
    op.create_index("ix_l3_source_units_source_hash", "l3_source_units", ["source_hash"])
    op.create_index(
        "ix_l3_source_units_last_raw_event_id",
        "l3_source_units",
        ["last_raw_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_l3_source_units_last_raw_event_id", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_source_hash", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_content_hash", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_external_id", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_item_key", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_source_stream", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_source_id", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_project_path", table_name="l3_source_units")
    op.drop_index("ix_l3_source_units_project_id", table_name="l3_source_units")
    op.drop_table("l3_source_units")
