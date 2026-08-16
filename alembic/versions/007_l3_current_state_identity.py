"""L3 current-state identity: fact_key + required source provenance.

Revision ID: 007_l3_current_state_identity
Revises: 006_source_unit_ledger
Create Date: 2026-08-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "007_l3_current_state_identity"
down_revision: str | None = "006_source_unit_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_KEY = "legacy_unattributed"


def _seed_legacy_sources() -> None:
    """Ensure every project has a legacy_unattributed source for unresolved provenance."""
    op.execute(
        sa.text(
            f"""
            INSERT INTO sources (
                id, project_id, project_path, source_key, source_type,
                display_name, connection_config, read_recipe, added_via, is_active,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                p.id,
                p.project_path,
                '{LEGACY_KEY}',
                'other',
                'Legacy unattributed provenance',
                '{{"kind": "legacy"}}'::jsonb,
                'Rows whose source could not be proven during migration; '
                'do not treat as an external crawl target.',
                'init',
                true,
                now(),
                now()
            FROM projects p
            WHERE NOT EXISTS (
                SELECT 1 FROM sources s
                WHERE s.project_id = p.id AND s.source_key = '{LEGACY_KEY}'
            )
            """
        )
    )


def upgrade() -> None:
    _seed_legacy_sources()

    # --- l3_facts.fact_key ---
    op.add_column("l3_facts", sa.Column("fact_key", sa.String(length=512), nullable=True))
    op.execute(sa.text("UPDATE l3_facts SET fact_key = 'legacy:' || id::text WHERE fact_key IS NULL"))
    op.alter_column("l3_facts", "fact_key", existing_type=sa.String(length=512), nullable=False)
    op.create_index("ix_l3_facts_fact_key", "l3_facts", ["fact_key"])
    op.create_unique_constraint("uq_l3_facts_project_key", "l3_facts", ["project_id", "fact_key"])

    # --- l3_distilled_knowledge.source_id ---
    op.add_column(
        "l3_distilled_knowledge",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE l3_distilled_knowledge d
            SET source_id = e.source_id
            FROM l4_raw_events e
            WHERE d.raw_event_id = e.id
              AND e.source_id IS NOT NULL
              AND d.source_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE l3_distilled_knowledge d
            SET source_id = s.id
            FROM sources s
            WHERE s.project_id = d.project_id
              AND s.source_key = '{LEGACY_KEY}'
              AND d.source_id IS NULL
            """
        )
    )
    op.alter_column(
        "l3_distilled_knowledge",
        "source_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_l3_distilled_knowledge_source_id",
        "l3_distilled_knowledge",
        "sources",
        ["source_id"],
        ["id"],
    )
    op.create_index("ix_l3_source_id", "l3_distilled_knowledge", ["source_id"])

    # --- backfill nullable source_id on facts / tasks / watched_refs ---
    for table in ("l3_facts", "l3_tasks", "l3_watched_refs"):
        op.execute(
            sa.text(
                f"""
                UPDATE {table} t
                SET source_id = s.id
                FROM sources s
                WHERE s.project_id = t.project_id
                  AND s.source_key = '{LEGACY_KEY}'
                  AND t.source_id IS NULL
                """
            )
        )
        op.alter_column(
            table,
            "source_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
        )


def downgrade() -> None:
    for table in ("l3_facts", "l3_tasks", "l3_watched_refs"):
        op.alter_column(
            table,
            "source_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )

    op.drop_index("ix_l3_source_id", table_name="l3_distilled_knowledge")
    op.drop_constraint(
        "fk_l3_distilled_knowledge_source_id",
        "l3_distilled_knowledge",
        type_="foreignkey",
    )
    op.drop_column("l3_distilled_knowledge", "source_id")

    op.drop_constraint("uq_l3_facts_project_key", "l3_facts", type_="unique")
    op.drop_index("ix_l3_facts_fact_key", table_name="l3_facts")
    op.drop_column("l3_facts", "fact_key")
