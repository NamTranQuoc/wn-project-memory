"""Switch embedding dimension 1536 -> 1024 (OpenAI text-embedding-3-small -> local bge-m3).

Old embeddings are from a different model's vector space and are meaningless
under the new one, so the column is dropped and re-added rather than cast —
this clears stale embeddings across all 5 embedding-bearing tables instead of
silently mixing two incompatible vector spaces in the same column. Rows and
their content_hash/source_hash are untouched; only `embedding` is reset to
NULL. Callers re-embed on next upsert (embedding is nullable everywhere it's
used).

Revision ID: 005_switch_embedding_dim
Revises: 004_remove_distillation
Create Date: 2026-08-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "005_switch_embedding_dim"
down_revision: str | None = "004_remove_distillation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_DIM = 1536
NEW_DIM = 1024

# (table, hnsw_index_name)
TABLES = [
    ("l3_distilled_knowledge", "ix_l3_embedding_hnsw"),
    ("l3_facts", "ix_l3_facts_embedding_hnsw"),
    ("l3_tasks", "ix_l3_tasks_embedding_hnsw"),
    ("l3_watched_refs", "ix_l3_watched_refs_embedding_hnsw"),
    ("l1_references", "ix_l1_references_embedding_hnsw"),
]


def _swap_dimension(dim: int) -> None:
    for table, index_name in TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.drop_column(table, "embedding")
        op.add_column(table, sa.Column("embedding", Vector(dim), nullable=True))
        op.execute(
            f"CREATE INDEX {index_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )


def upgrade() -> None:
    _swap_dimension(NEW_DIM)


def downgrade() -> None:
    _swap_dimension(OLD_DIM)
