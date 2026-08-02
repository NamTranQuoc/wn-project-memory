"""Rename L1 working memory to L0, add l1_references, add watched_refs.status_note.

Revision ID: 003_l0_l1_restructure
Revises: 002_projects_ops
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "003_l0_l1_restructure"
down_revision: str | None = "002_projects_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    # --- 1. l1_working_memory -> l0_working_memory: rename the REAL DB objects ---
    op.rename_table("l1_working_memory", "l0_working_memory")
    # op.rename_table only renames the table itself — the PK constraint (and its
    # backing index) keep their auto-generated l1_working_memory_pkey name unless
    # renamed explicitly too.
    op.execute(
        "ALTER TABLE l0_working_memory "
        "RENAME CONSTRAINT l1_working_memory_pkey TO l0_working_memory_pkey"
    )
    op.execute(
        "ALTER TABLE l0_working_memory RENAME CONSTRAINT uq_l1_project_path TO uq_l0_project_path"
    )
    op.execute("ALTER INDEX ix_l1_project_path RENAME TO ix_l0_project_path")
    op.execute(
        "ALTER INDEX ix_l1_working_memory_project_id RENAME TO ix_l0_working_memory_project_id"
    )
    op.execute(
        "ALTER TABLE l0_working_memory "
        "RENAME CONSTRAINT fk_l1_working_memory_project_id "
        "TO fk_l0_working_memory_project_id"
    )

    # --- 2. l1_references (new) ---
    op.create_table(
        "l1_references",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("ref_key", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_policy", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("raw_event_id", sa.UUID(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "ref_key", name="uq_l1_references_project_ref_key"),
    )
    op.create_index("ix_l1_references_project_path", "l1_references", ["project_path"])
    op.create_index("ix_l1_references_project_id", "l1_references", ["project_id"])
    op.create_index("ix_l1_references_ref_key", "l1_references", ["ref_key"])
    op.create_index(
        "ix_l1_references_project_is_policy",
        "l1_references",
        ["project_id", "is_policy"],
    )
    op.create_index("ix_l1_references_source_id", "l1_references", ["source_id"])
    op.execute(
        "CREATE INDEX ix_l1_references_content_trgm ON l1_references "
        "USING gin (content gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_l1_references_embedding_hnsw ON l1_references "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # --- 3. l3_watched_refs.status_note ---
    op.add_column("l3_watched_refs", sa.Column("status_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("l3_watched_refs", "status_note")

    op.execute("DROP INDEX IF EXISTS ix_l1_references_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_l1_references_content_trgm")
    op.drop_index("ix_l1_references_source_id", table_name="l1_references")
    op.drop_index("ix_l1_references_project_is_policy", table_name="l1_references")
    op.drop_index("ix_l1_references_ref_key", table_name="l1_references")
    op.drop_index("ix_l1_references_project_id", table_name="l1_references")
    op.drop_index("ix_l1_references_project_path", table_name="l1_references")
    op.drop_table("l1_references")

    op.execute(
        "ALTER TABLE l0_working_memory "
        "RENAME CONSTRAINT fk_l0_working_memory_project_id "
        "TO fk_l1_working_memory_project_id"
    )
    op.execute(
        "ALTER INDEX ix_l0_working_memory_project_id RENAME TO ix_l1_working_memory_project_id"
    )
    op.execute("ALTER INDEX ix_l0_project_path RENAME TO ix_l1_project_path")
    op.execute(
        "ALTER TABLE l0_working_memory RENAME CONSTRAINT uq_l0_project_path TO uq_l1_project_path"
    )
    op.execute(
        "ALTER TABLE l0_working_memory "
        "RENAME CONSTRAINT l0_working_memory_pkey TO l1_working_memory_pkey"
    )
    op.rename_table("l0_working_memory", "l1_working_memory")
