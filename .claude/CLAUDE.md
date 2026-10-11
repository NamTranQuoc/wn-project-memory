# Core implementation rules

## 1. Security, configuration, privacy
- **No hardcoded secrets.** Load settings from `src/core/config.py` (`pydantic-settings`).
- **Env files.** `.env.example` holds placeholders only and is committed. Real values live in
  `.env.local` (read after `.env`, wins). Both `.env` and `.env.local` are git-ignored.
- **API key is mandatory.** The service listens on HTTP only (REST and MCP at `/mcp`).
  It refuses to start without a `MEMORY_API_KEY` of at least 32 characters. Only `/health` and
  `/ready` are public.
- **Outbound URLs.** `LITELLM_API_BASE` and `EMBEDDING_API_BASE` may use `http` only on loopback;
  remote endpoints must be `https`. Plugin setup accepts only `https` for a remote MCP URL.
- **Public repo.** Tracked files, commit messages and the Docker image carry no personal or
  sensitive data: no secrets, passwords or tokens; no real e-mails, Teams/Jira links, home
  paths, colleague names, company or internal repo names; no personal domains, VPS or
  private-machine hostnames, no IP addresses other than loopback. The commit author name and
  e-mail are the only exception. Tests and docs use `example.com`, `/work/payments`,
  "Dana Tran". Before every commit: `make privacy-check` (also the pre-commit hook,
  `make hooks`) must pass, and the staged diff and commit message are read for the items above
  that no pattern catches. New personal strings go into the local `.privacy-denylist`.
- **History before `31a4ccf`.** Privacy findings in commits older than `31a4ccf` are accepted
  and out of scope: do not report them, purge them or recreate the repository for them.
- **Secrets never enter memory.** Two layers. (1) The skill, the MCP instructions and the
  write tools tell the agent never to send a secret. (2) Every write path redacts with pure
  regex (`secret_redactor.py`), never with an LLM: `redact_secrets` for raw payloads
  (`add_raw_event` is the only writer of `l4_raw_events`), ingest `unit_metadata` and
  embedding input; `redact_known_secrets` for curated text (facts and revisions, notes,
  global notes, people, teams, source url/recipe, source links); secret-named keys of
  JSON fields (`connection_config`, `attrs`, `links`) are masked. Hashes are taken from the
  text the agent sent.

## 2. Data model and database
- **Layers.** L1 `l1_projects`/`l1_notes`/`l1_sources` · L2 `l2_namespaces` · L3 `l3_facts`,
  `l3_revisions`, `l3_revision_sources`, `l3_links` · L4 `l4_raw_events` · ingest ledger
  `ingest_units`/`ingest_watermarks` · global `g_*`. Every project row hangs off `l1_projects`
  (one row per `project_path`); global tables have no project.
- **Fact keys.** Every L3 key must match one L2 namespace template; the service rejects others.
- **History.** A write to a fact adds a revision with a full snapshot, change type, reason and
  sources (real `source_url` + `raw_event_id`). The service never merges text; the agent does and
  passes `base_revision`. A stale `base_revision` is refused.
- **L4 retention.** `l4_raw_events` is partitioned by `last_touched_at`. A full read
  (`get_raw_context`) or a citation in a new revision touches the row (at most once per
  `L4_TOUCH_MIN_DAYS`), which moves it to the current partition. The scheduler creates next
  month's partition and drops partitions older than `L4_IDLE_RETENTION_MONTHS` (default 6).
- **Strict indexing.** No full table scans: B-tree on lookup keys, GIN on the generated
  `search_tsv` columns (full-text, `simple` config) of facts and namespaces, GIN `pg_trgm`
  on fact titles and name-searched text, HNSW on `embedding`.
- **Database user.** The service connects as its own non-superuser user, prepared once by an
  admin with `bin/create-db-user.sh` (database owner, `vector`/`pg_trgm`, the NOLOGIN
  `SQL_READER_ROLE` granted to it). Migrations run as that user and create no roles or
  users; they only grant on their own tables.
- **Migrations.** Alembic, one file per change; `alembic check` must report no drift. Monthly L4
  partitions are not part of migrations.

## 3. Resilience and context-window protection
- **Semantic search is optional.** `SEMANTIC_SEARCH_ENABLED=false` (default): no embedding call
  anywhere and no `semantic_search` tool; keyword search stays. All embedding goes through
  `services/embedding.py`. Clients retry with `tenacity`.
- **Agent-driven writes.** No background LLM work. The agent extracts facts and writes them.
- **Policies need the user.** Adding, changing or removing a policy note goes through
  `l1_service.upsert_note(approved_via=…)`: MCP asks the user with an elicitation dialog
  (SDK `Resolve` + `Elicit`, so both the mid-call and the input-required protocol work);
  REST counts as the key holder's approval. Every policy row has `approved_at`.
- **Memory is data.** The skill and the MCP instructions say that stored text is never an
  instruction; only approved policies bind.
- **Write limits.** Raw payload ≤ `MAX_RAW_CHARS` (200 000), fact/note content ≤
  `MAX_TEXT_CHARS` (20 000), checked with `check_length`.
- **Output limits.** Lists and searches default to 5 rows (search hard cap 5, lists pageable).
- **Truncation.** Text over 1500 chars ends with `... [truncated, use get_raw_context to read full]`.
  `get_raw_context` returns pages (`RAW_PAGE_CHARS`, at most `MAX_RAW_PAGE_CHARS`).
- **Few calls.** `get_project_overview` carries policies and namespaces; `write_fact` takes
  `namespace` + `parts` and needs no read before a create; `write_facts` takes up to 10;
  search hits carry a snippet.
- **Safe SQL.** `query_deep_memory_sql` reads only the caller's `l4_raw_events`: a regex
  check (single SELECT, allow-listed functions only), a query-plan check, and the role
  `agentic_memory_sql_reader` (SELECT on that table only), in a read-only transaction with
  a 5 s timeout and `LIMIT 10` forced.
- **LIKE.** User text in `ilike` goes through `contains_pattern` (escapes `%`, `_`).

## 4. Code style and architecture
- Python 3.10+, full type hints. Layers: `models/`, `services/` (logic), `routers/` (REST),
  `mcp_server.py` (MCP tools), `core/` (config, db, auth, scheduler). MCP tools and REST routes
  call the same service functions.
- Diagrams in `README.md` are generated from `docs/diagrams/*.json` with archify (`make diagrams`).
