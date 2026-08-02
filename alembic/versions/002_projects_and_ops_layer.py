"""Add projects registry, sources, project_id FKs, and L3-Ops tables.

Revision ID: 002_projects_ops
Revises: 001_initial
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002_projects_ops"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536

SOURCE_TYPE = postgresql.ENUM(
    "github_pr",
    "github_repo",
    "teams_chat",
    "teams_dm",
    "jira",
    "local_file",
    "user_session",
    "other",
    name="source_type_enum",
    create_type=False,
)
SOURCE_ADDED_VIA = postgresql.ENUM(
    "init", "manual", name="source_added_via_enum", create_type=False
)
FACT_KIND = postgresql.ENUM(
    "fact",
    "decision",
    "plan",
    "question",
    "issue",
    "solution",
    name="fact_kind_enum",
    create_type=False,
)
TASK_STATUS = postgresql.ENUM(
    "open", "partial", "closed", name="task_status_enum", create_type=False
)
WATCHED_REF_TYPE = postgresql.ENUM(
    "pr",
    "issue",
    "sha",
    "path",
    "ticket",
    "tag",
    "other",
    name="watched_ref_type_enum",
    create_type=False,
)
WATCHED_REF_DISPOSITION = postgresql.ENUM(
    "mine",
    "queued",
    "resolved",
    name="watched_ref_disposition_enum",
    create_type=False,
)


def upgrade() -> None:
    # --- enums ---
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE source_type_enum AS ENUM (
                'github_pr', 'github_repo', 'teams_chat', 'teams_dm',
                'jira', 'local_file', 'user_session', 'other'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE source_added_via_enum AS ENUM ('init', 'manual');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE fact_kind_enum AS ENUM (
                'fact', 'decision', 'plan', 'question', 'issue', 'solution'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE task_status_enum AS ENUM ('open', 'partial', 'closed');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE watched_ref_type_enum AS ENUM (
                'pr', 'issue', 'sha', 'path', 'ticket', 'tag', 'other'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE watched_ref_disposition_enum AS ENUM (
                'mine', 'queued', 'resolved'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )

    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_path", name="uq_projects_project_path"),
    )
    op.create_index("ix_projects_project_path", "projects", ["project_path"])

    # Backfill projects from existing tables
    op.execute(
        """
        INSERT INTO projects (id, project_path, display_name, created_at, updated_at)
        SELECT gen_random_uuid(), p.project_path,
               COALESCE(NULLIF(split_part(p.project_path, '/', -1), ''), p.project_path),
               now(), now()
        FROM (
            SELECT DISTINCT project_path FROM l1_working_memory
            UNION
            SELECT DISTINCT project_path FROM l2_meta_memory
            UNION
            SELECT DISTINCT project_path FROM l3_distilled_knowledge
            UNION
            SELECT DISTINCT project_path FROM l4_raw_events
        ) AS p
        WHERE p.project_path IS NOT NULL AND p.project_path <> ''
        ON CONFLICT (project_path) DO NOTHING;
        """
    )

    # --- sources (needed before L4.source_id FK) ---
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("source_key", sa.String(length=256), nullable=False),
        sa.Column("source_type", SOURCE_TYPE, nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("connection_config", postgresql.JSONB(), nullable=True),
        sa.Column("read_recipe", sa.Text(), nullable=True),
        sa.Column("added_via", SOURCE_ADDED_VIA, nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "source_key", name="uq_sources_project_key"),
    )
    op.create_index("ix_sources_project_id", "sources", ["project_id"])
    op.create_index("ix_sources_project_path", "sources", ["project_path"])
    op.create_index("ix_sources_source_key", "sources", ["source_key"])

    # Seed user_session source per project
    op.execute(
        """
        INSERT INTO sources (
            id, project_id, project_path, source_key, source_type, display_name,
            connection_config, read_recipe, added_via, is_active, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            p.id,
            p.project_path,
            'user_session',
            'user_session',
            'User session decisions',
            '{"kind":"live_chat"}'::jsonb,
            'Live agent session decisions — no external re-fetch; content is the chat utterance itself.',
            'init',
            true,
            now(),
            now()
        FROM projects p
        ON CONFLICT (project_id, source_key) DO NOTHING;
        """
    )

    # --- add project_id to existing tables ---
    for table in (
        "l1_working_memory",
        "l2_meta_memory",
        "l3_distilled_knowledge",
        "l4_raw_events",
    ):
        op.add_column(table, sa.Column("project_id", sa.UUID(), nullable=True))
        op.execute(
            f"""
            UPDATE {table} t
            SET project_id = p.id
            FROM projects p
            WHERE t.project_path = p.project_path;
            """
        )
        # Orphan rows (should not happen): create project then backfill
        op.execute(
            f"""
            INSERT INTO projects (id, project_path, display_name, created_at, updated_at)
            SELECT gen_random_uuid(), t.project_path, t.project_path, now(), now()
            FROM (SELECT DISTINCT project_path FROM {table} WHERE project_id IS NULL) t
            ON CONFLICT (project_path) DO NOTHING;
            """
        )
        op.execute(
            f"""
            UPDATE {table} t
            SET project_id = p.id
            FROM projects p
            WHERE t.project_path = p.project_path AND t.project_id IS NULL;
            """
        )
        op.alter_column(table, "project_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_project_id",
            table,
            "projects",
            ["project_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])

    # L4 source_id (nullable FK to sources)
    op.add_column("l4_raw_events", sa.Column("source_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_l4_raw_events_source_id",
        "l4_raw_events",
        "sources",
        ["source_id"],
        ["id"],
    )
    op.create_index("ix_l4_source_id", "l4_raw_events", ["source_id"])
    # Default existing L4 rows to user_session source for their project
    op.execute(
        """
        UPDATE l4_raw_events e
        SET source_id = s.id
        FROM sources s
        WHERE e.project_id = s.project_id
          AND s.source_key = 'user_session'
          AND e.source_id IS NULL;
        """
    )

    # --- l3_watermarks ---
    op.create_table(
        "l3_watermarks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("raw_event_id", sa.UUID(), nullable=True),
        sa.Column("l3_entity_id", sa.UUID(), nullable=True),
        sa.Column(
            "stream_key",
            sa.String(length=256),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("indexed_through", postgresql.JSONB(), nullable=True),
        sa.Column("full_read_ids", postgresql.JSONB(), nullable=True),
        sa.Column("known_gaps", postgresql.JSONB(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
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
        sa.ForeignKeyConstraint(["l3_entity_id"], ["l3_distilled_knowledge.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "source_id",
            "stream_key",
            name="uq_l3_watermarks_project_source_stream",
        ),
    )
    op.create_index("ix_l3_watermarks_project_id", "l3_watermarks", ["project_id"])
    op.create_index("ix_l3_watermarks_project_path", "l3_watermarks", ["project_path"])
    op.create_index("ix_l3_watermarks_source_id", "l3_watermarks", ["source_id"])
    op.create_index("ix_l3_watermarks_checked_at", "l3_watermarks", ["checked_at"])

    # --- l3_facts ---
    op.create_table(
        "l3_facts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("raw_event_id", sa.UUID(), nullable=True),
        sa.Column("l3_entity_id", sa.UUID(), nullable=True),
        sa.Column("kind", FACT_KIND, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(["l3_entity_id"], ["l3_distilled_knowledge.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_l3_facts_project_id", "l3_facts", ["project_id"])
    op.create_index("ix_l3_facts_project_path", "l3_facts", ["project_path"])
    op.create_index("ix_l3_facts_kind", "l3_facts", ["kind"])
    op.create_index("ix_l3_facts_project_occurred", "l3_facts", ["project_id", "occurred_at"])
    op.create_index("ix_l3_facts_project_priority", "l3_facts", ["project_id", "priority"])
    op.create_index("ix_l3_facts_source_id", "l3_facts", ["source_id"])
    op.create_index("ix_l3_facts_raw_event_id", "l3_facts", ["raw_event_id"])
    op.create_index("ix_l3_facts_l3_entity_id", "l3_facts", ["l3_entity_id"])
    op.execute("CREATE INDEX ix_l3_facts_content_trgm ON l3_facts USING gin (content gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_l3_facts_embedding_hnsw ON l3_facts "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # --- l3_tasks ---
    op.create_table(
        "l3_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("raw_event_id", sa.UUID(), nullable=True),
        sa.Column("l3_entity_id", sa.UUID(), nullable=True),
        sa.Column("task_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", TASK_STATUS, nullable=False),
        sa.Column(
            "priority",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("waiting_on", sa.String(length=512), nullable=True),
        sa.Column(
            "since_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["l3_entity_id"], ["l3_distilled_knowledge.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "task_key", name="uq_l3_tasks_project_key"),
    )
    op.create_index("ix_l3_tasks_project_id", "l3_tasks", ["project_id"])
    op.create_index("ix_l3_tasks_project_path", "l3_tasks", ["project_path"])
    op.create_index("ix_l3_tasks_status", "l3_tasks", ["status"])
    op.create_index("ix_l3_tasks_project_priority", "l3_tasks", ["project_id", "priority"])
    op.create_index("ix_l3_tasks_since_at", "l3_tasks", ["since_at"])
    op.create_index("ix_l3_tasks_source_id", "l3_tasks", ["source_id"])
    op.execute("CREATE INDEX ix_l3_tasks_content_trgm ON l3_tasks USING gin (content gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_l3_tasks_embedding_hnsw ON l3_tasks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # --- l3_watched_refs ---
    op.create_table(
        "l3_watched_refs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("project_path", sa.String(length=1024), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("raw_event_id", sa.UUID(), nullable=True),
        sa.Column("l3_entity_id", sa.UUID(), nullable=True),
        sa.Column("ref_type", WATCHED_REF_TYPE, nullable=False),
        sa.Column("ref_value", sa.String(length=1024), nullable=False),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("disposition", WATCHED_REF_DISPOSITION, nullable=False),
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
        sa.ForeignKeyConstraint(["l3_entity_id"], ["l3_distilled_knowledge.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "ref_type",
            "ref_value",
            name="uq_l3_watched_refs_project_type_value",
        ),
    )
    op.create_index("ix_l3_watched_refs_project_id", "l3_watched_refs", ["project_id"])
    op.create_index("ix_l3_watched_refs_project_path", "l3_watched_refs", ["project_path"])
    op.create_index("ix_l3_watched_refs_disposition", "l3_watched_refs", ["disposition"])
    op.create_index("ix_l3_watched_refs_source_id", "l3_watched_refs", ["source_id"])
    op.execute(
        "CREATE INDEX ix_l3_watched_refs_why_trgm ON l3_watched_refs USING gin (why gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_l3_watched_refs_embedding_hnsw ON l3_watched_refs "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("l3_watched_refs")
    op.drop_table("l3_tasks")
    op.drop_table("l3_facts")
    op.drop_table("l3_watermarks")

    op.drop_constraint("fk_l4_raw_events_source_id", "l4_raw_events", type_="foreignkey")
    op.drop_index("ix_l4_source_id", table_name="l4_raw_events")
    op.drop_column("l4_raw_events", "source_id")

    for table in (
        "l4_raw_events",
        "l3_distilled_knowledge",
        "l2_meta_memory",
        "l1_working_memory",
    ):
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_column(table, "project_id")

    op.drop_table("sources")
    op.drop_table("projects")

    op.execute("DROP TYPE IF EXISTS watched_ref_disposition_enum")
    op.execute("DROP TYPE IF EXISTS watched_ref_type_enum")
    op.execute("DROP TYPE IF EXISTS task_status_enum")
    op.execute("DROP TYPE IF EXISTS fact_kind_enum")
    op.execute("DROP TYPE IF EXISTS source_added_via_enum")
    op.execute("DROP TYPE IF EXISTS source_type_enum")
