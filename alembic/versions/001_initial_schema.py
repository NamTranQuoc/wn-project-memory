"""Initial 4-layer memory schema with L4 partitioning.

Revision ID: 001_initial
Revises:
Create Date: 2026-08-01 00:00:00.000000
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


def _month_bounds(dt: datetime) -> tuple[datetime, datetime]:
    start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE distillation_status_enum AS ENUM ('pending', 'processed', 'failed');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.create_table(
        "l1_working_memory",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("current_focus_text", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_path", name="uq_l1_project_path"),
    )
    op.create_index("ix_l1_project_path", "l1_working_memory", ["project_path"])

    op.create_table(
        "l2_meta_memory",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("environment_setup", sa.Text(), nullable=True),
        sa.Column("project_structure", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_path", name="uq_l2_project_path"),
    )
    op.create_index("ix_l2_project_path", "l2_meta_memory", ["project_path"])

    op.create_table(
        "l3_distilled_knowledge",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("entity_key", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_event_id", sa.UUID(), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "last_verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_path", "entity_key", name="uq_l3_project_entity"),
    )
    op.create_index("ix_l3_project_path", "l3_distilled_knowledge", ["project_path"])
    op.create_index("ix_l3_raw_event_id", "l3_distilled_knowledge", ["raw_event_id"])
    op.create_index("ix_l3_content_hash", "l3_distilled_knowledge", ["content_hash"])
    op.create_index("ix_l3_source_hash", "l3_distilled_knowledge", ["source_hash"])
    op.execute(
        "CREATE INDEX ix_l3_content_trgm ON l3_distilled_knowledge "
        "USING gin (content gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_l3_embedding_hnsw ON l3_distilled_knowledge "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # Partitioned L4 parent table — composite PK required for RANGE partitioning
    op.execute(
        """
        CREATE TABLE l4_raw_events (
            id UUID NOT NULL,
            project_path VARCHAR(1024) NOT NULL,
            event_type VARCHAR(128) NOT NULL,
            raw_content TEXT NOT NULL,
            source_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            distillation_status distillation_status_enum NOT NULL DEFAULT 'pending',
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        """
    )
    op.execute(
        "CREATE INDEX ix_l4_project_created ON l4_raw_events (project_path, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_l4_distillation_status ON l4_raw_events (distillation_status)"
    )

    now = datetime.now(timezone.utc)
    # Current month + next month partitions
    for year, month in [(now.year, now.month)]:
        start, end = _month_bounds(datetime(year, month, 1, tzinfo=timezone.utc))
        part_name = f"l4_raw_events_{start.year}_{start.month:02d}"
        op.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {part_name}
            PARTITION OF l4_raw_events
            FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}');
            """
        )

    # Next month
    if now.month == 12:
        next_dt = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_dt = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    start, end = _month_bounds(next_dt)
    part_name = f"l4_raw_events_{start.year}_{start.month:02d}"
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {part_name}
        PARTITION OF l4_raw_events
        FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS l4_raw_events CASCADE")
    op.drop_table("l3_distilled_knowledge")
    op.drop_table("l2_meta_memory")
    op.drop_table("l1_working_memory")
    op.execute("DROP TYPE IF EXISTS distillation_status_enum")
