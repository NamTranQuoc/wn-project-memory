"""Remove automatic LLM-based distillation: drop l4_raw_events.distillation_status.

Extraction is now agent-driven (the calling agent reads the raw event itself and
calls upsert_fact/upsert_task/upsert_watched_ref/upsert_distilled_rule directly)
instead of an automatic background LLM distillation pass, so the status column
and its enum type are no longer written or read by anything.

Revision ID: 004_remove_distillation
Revises: 003_l0_l1_restructure
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "004_remove_distillation"
down_revision: str | None = "003_l0_l1_restructure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_l4_distillation_status")
    op.execute("ALTER TABLE l4_raw_events DROP COLUMN IF EXISTS distillation_status")
    op.execute("DROP TYPE IF EXISTS distillation_status_enum")


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE distillation_status_enum AS ENUM ('pending', 'processed', 'failed');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE l4_raw_events ADD COLUMN distillation_status "
        "distillation_status_enum NOT NULL DEFAULT 'pending'"
    )
    op.execute("CREATE INDEX ix_l4_distillation_status ON l4_raw_events (distillation_status)")
