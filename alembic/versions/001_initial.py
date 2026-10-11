"""Initial schema: L1 project, L2 namespaces, L3 facts with revisions, L4 raw events,
ingest ledger and the global store.

Revision ID: 001_initial
Revises:
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from src.core.config import get_settings
from src.services.partition_service import next_month, partition_ddl

revision: str = "001_initial"
down_revision: str | None = None
branch_labels = None
depends_on = None

SOURCE_TYPES = ("github_pr", "github_repo", "teams_chat", "teams_dm", "jira", "local_file",
                "user_session", "other")
FACT_KINDS = ("fact", "decision", "plan", "question", "issue", "solution", "rule", "task",
              "watched_ref")
CHANGE_TYPES = ("create", "replace", "merge", "close", "reopen")


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(),
                     nullable=nullable)


def _fk(name: str, target: str, *, ondelete: str | None = "CASCADE", nullable: bool = False,
        primary_key: bool = False) -> sa.Column:
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey(target, ondelete=ondelete),
                     nullable=nullable, primary_key=primary_key)


# Full-text columns ('simple': no stemming, works for any language). Keys and paths are
# split on '.' and '/' so "decision.api" matches "api". Mirrored in src/models.
NAMESPACE_TSV = (
    "setweight(to_tsvector('simple'::regconfig, translate(namespace, './', '  ')), 'A') || "
    "setweight(to_tsvector('simple'::regconfig, translate(summary, './', '  ')), 'B')"
)
FACT_TSV = (
    "setweight(to_tsvector('simple'::regconfig, "
    "translate(fact_key || ' ' || title, './', '  ')), 'A') || "
    "setweight(to_tsvector('simple'::regconfig, translate(summary, './', '  ')), 'B') || "
    "setweight(to_tsvector('simple'::regconfig, translate(content, './', '  ')), 'C')"
)


def _trgm(name: str, table: str, column: str) -> None:
    op.create_index(name, table, [column], postgresql_using="gin",
                    postgresql_ops={column: "gin_trgm_ops"})


def _grant_sql_reader() -> None:
    """query_deep_memory_sql runs as SQL_READER_ROLE: it can read l4_raw_events and nothing
    else. The role exists before migrations run (bin/create-db-user.sh); this only grants.
    Partitions need no grant; a query through the parent checks the parent's privileges.
    """
    role = get_settings().sql_reader_role
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                RAISE EXCEPTION 'role {role} does not exist'
                    USING HINT = 'create the database user with bin/create-db-user.sh first';
            END IF;
        END $$""")
    op.execute(f"GRANT SELECT ON l4_raw_events TO {role}")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    source_type = pg.ENUM(*SOURCE_TYPES, name="source_type_enum", create_type=False)
    fact_kind = pg.ENUM(*FACT_KINDS, name="fact_kind_enum", create_type=False)
    change_type = pg.ENUM(*CHANGE_TYPES, name="change_type_enum", create_type=False)
    for enum in (source_type, fact_kind, change_type):
        enum.create(op.get_bind(), checkfirst=True)

    # ── L1 ──
    op.create_table(
        "l1_projects",
        _uuid_pk(),
        sa.Column("project_path", sa.String(1024), nullable=False, unique=True),
        sa.Column("display_name", sa.Text()),
        sa.Column("overview", sa.Text()),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "l1_notes",
        _uuid_pk(),
        _fk("project_id", "l1_projects.id"),
        sa.Column("note_key", sa.String(256), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_policy", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_via", sa.String(32)),
        _ts("updated_at"),
        sa.CheckConstraint("NOT is_policy OR approved_at IS NOT NULL",
                           name="ck_l1_notes_policy_approved"),
    )
    op.create_index("uq_l1_notes_project_key", "l1_notes", ["project_id", "note_key"], unique=True)
    op.create_index("ix_l1_notes_project_policy", "l1_notes",
                    ["project_id", "is_policy", "priority"])
    op.create_table(
        "l1_sources",
        _uuid_pk(),
        _fk("project_id", "l1_projects.id"),
        sa.Column("source_key", sa.String(256), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("connection_config", pg.JSONB()),
        sa.Column("read_recipe", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _ts("updated_at"),
    )
    op.create_index("uq_l1_sources_project_key", "l1_sources", ["project_id", "source_key"],
                    unique=True)

    # ── L2 ──
    op.create_table(
        "l2_namespaces",
        _uuid_pk(),
        _fk("project_id", "l1_projects.id"),
        sa.Column("namespace", sa.String(256), nullable=False),
        sa.Column("key_template", sa.String(512), nullable=False),
        sa.Column("default_kind", fact_kind, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("keywords", pg.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("search_tsv", pg.TSVECTOR(), sa.Computed(NAMESPACE_TSV, persisted=True)),
        _ts("updated_at"),
    )
    op.create_index("uq_l2_namespaces_project_ns", "l2_namespaces", ["project_id", "namespace"],
                    unique=True)
    op.create_index("ix_l2_namespaces_keywords", "l2_namespaces", ["keywords"],
                    postgresql_using="gin")
    op.create_index("ix_l2_namespaces_search", "l2_namespaces", ["search_tsv"],
                    postgresql_using="gin")

    # ── L3 ──
    op.create_table(
        "l3_facts",
        _uuid_pk(),
        _fk("project_id", "l1_projects.id"),
        _fk("namespace_id", "l2_namespaces.id", ondelete=None),
        sa.Column("fact_key", sa.String(512), nullable=False),
        sa.Column("kind", fact_kind, nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.String(280), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("attrs", pg.JSONB()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(get_settings().embedding_dimensions)),
        sa.Column("search_tsv", pg.TSVECTOR(), sa.Computed(FACT_TSV, persisted=True)),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("uq_l3_facts_project_key", "l3_facts", ["project_id", "fact_key"], unique=True)
    op.create_index("ix_l3_facts_namespace_updated", "l3_facts", ["namespace_id", "updated_at"])
    op.create_index("ix_l3_facts_search", "l3_facts", ["search_tsv"], postgresql_using="gin")
    # Typo-tolerant fallback on titles.
    _trgm("ix_l3_facts_title_trgm", "l3_facts", "title")
    op.create_index("ix_l3_facts_embedding_hnsw", "l3_facts", ["embedding"],
                    postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 64},
                    postgresql_ops={"embedding": "vector_cosine_ops"})

    op.create_table(
        "l3_revisions",
        _uuid_pk(),
        _fk("fact_id", "l3_facts.id"),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("change_type", change_type, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.String(280), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("attrs", pg.JSONB()),
        _ts("changed_at"),
    )
    op.create_index("uq_l3_revisions_fact_rev", "l3_revisions", ["fact_id", "revision"],
                    unique=True)

    op.create_table(
        "l3_revision_sources",
        _uuid_pk(),
        _fk("revision_id", "l3_revisions.id"),
        _fk("source_id", "l1_sources.id", ondelete=None),
        sa.Column("source_url", sa.Text()),
        sa.Column("raw_event_id", pg.UUID(as_uuid=True)),
        sa.Column("source_hash", sa.String(64)),
    )
    op.create_index("ix_l3_revision_sources_revision", "l3_revision_sources", ["revision_id"])
    op.create_index("ix_l3_revision_sources_raw", "l3_revision_sources", ["raw_event_id"])
    op.create_index("ix_l3_revision_sources_source", "l3_revision_sources", ["source_id"])

    op.create_table(
        "l3_links",
        _fk("fact_id", "l3_facts.id", primary_key=True),
        _fk("related_fact_id", "l3_facts.id", primary_key=True),
        sa.Column("relation", sa.String(32), primary_key=True),
        _ts("created_at"),
    )
    op.create_index("ix_l3_links_related", "l3_links", ["related_fact_id"])

    # ── L4 (partitioned by month of last touch) ──
    op.create_table(
        "l4_raw_events",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        _fk("project_id", "l1_projects.id"),
        sa.Column("project_path", sa.String(1024), nullable=False),
        _fk("source_id", "l1_sources.id", ondelete="SET NULL", nullable=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_url", sa.Text()),
        _ts("created_at"),
        _ts("last_touched_at"),
        sa.PrimaryKeyConstraint("id", "last_touched_at"),
        postgresql_partition_by="RANGE (last_touched_at)",
    )
    op.create_index("ix_l4_raw_events_id", "l4_raw_events", ["id"])
    op.create_index("ix_l4_raw_events_project_created", "l4_raw_events",
                    ["project_path", "created_at"])
    op.create_index("ix_l4_raw_events_source", "l4_raw_events", ["source_id"])
    now = datetime.now(timezone.utc)
    for dt in (now, next_month(now)):
        op.execute(partition_ddl(dt))
    _grant_sql_reader()

    # ── ingest ledger ──
    op.create_table(
        "ingest_units",
        _uuid_pk(),
        _fk("source_id", "l1_sources.id"),
        sa.Column("stream_key", sa.String(256), nullable=False, server_default=sa.text("''")),
        sa.Column("item_key", sa.String(512), nullable=False),
        sa.Column("external_id", sa.String(512)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("last_raw_event_id", pg.UUID(as_uuid=True)),
        sa.Column("unit_metadata", pg.JSONB()),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
    )
    op.create_index("uq_ingest_units_source_stream_item", "ingest_units",
                    ["source_id", "stream_key", "item_key"], unique=True)
    op.create_index("ix_ingest_units_source_stream_hash", "ingest_units",
                    ["source_id", "stream_key", "source_hash"])
    op.create_table(
        "ingest_watermarks",
        _fk("source_id", "l1_sources.id", primary_key=True),
        sa.Column("stream_key", sa.String(256), primary_key=True, server_default=sa.text("''")),
        sa.Column("indexed_through", pg.JSONB()),
        sa.Column("full_read_ids", pg.JSONB()),
        sa.Column("known_gaps", pg.JSONB()),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        _ts("updated_at"),
    )

    # ── global ──
    op.create_table(
        "g_people",
        _uuid_pk(),
        sa.Column("person_key", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("address_as", sa.Text()),
        sa.Column("dm_note", sa.Text()),
        sa.Column("links", pg.JSONB()),
        _ts("updated_at"),
    )
    _trgm("ix_g_people_display_name_trgm", "g_people", "display_name")
    op.create_table(
        "g_identities",
        sa.Column("system", sa.String(32), primary_key=True),
        sa.Column("handle", sa.String(256), primary_key=True),
        _fk("person_id", "g_people.id"),
        sa.Column("url", sa.Text()),
    )
    op.create_index("ix_g_identities_person", "g_identities", ["person_id"])
    op.create_table(
        "g_teams",
        _uuid_pk(),
        sa.Column("team_key", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("links", pg.JSONB()),
        _ts("updated_at"),
    )
    op.create_table(
        "g_team_members",
        _fk("team_id", "g_teams.id", primary_key=True),
        _fk("person_id", "g_people.id", primary_key=True),
        sa.Column("role", sa.String(64)),
    )
    op.create_index("ix_g_team_members_person", "g_team_members", ["person_id"])
    op.create_table(
        "g_notes",
        sa.Column("note_key", sa.String(256), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        _ts("updated_at"),
    )
    _trgm("ix_g_notes_title_trgm", "g_notes", "title")


def downgrade() -> None:
    for table in ("g_notes", "g_team_members", "g_teams", "g_identities", "g_people",
                  "ingest_watermarks", "ingest_units", "l4_raw_events", "l3_links",
                  "l3_revision_sources", "l3_revisions", "l3_facts", "l2_namespaces",
                  "l1_sources", "l1_notes", "l1_projects"):
        op.drop_table(table)
    for enum in ("change_type_enum", "fact_kind_enum", "source_type_enum"):
        op.execute(f"DROP TYPE IF EXISTS {enum}")
