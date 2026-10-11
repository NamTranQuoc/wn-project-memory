# Agentic Memory

Layered project memory for coding agents. A Claude Code plugin connects the agent over MCP
(HTTP) to a small service that keeps, per project:

- **L1 · Project** — what the project is, notes, binding policies, and its sources (chat
  groups, Jira, repos, the plan folder).
- **L2 · Namespaces** — the key map: each namespace owns a key template such as
  `decision.api.{service}.{topic}` and a one-line summary.
- **L3 · Facts** — one current value per key, plus every past revision with its time,
  change type, reason, the real source link and the raw copy it came from.
- **L4 · Raw events** — the raw payloads, kept until 6 months after they were last read
  or cited.

A **global** directory, shared by all projects, holds people (how to address them in a DM,
their accounts on Teams/GitHub/Jira), teams and members, and global notes.

![Architecture](docs/diagrams/architecture.svg)

## How the agent uses it

The agent always reads top-down and always builds keys from L2, so a later write lands on
the same fact as an earlier read.

![Layers](docs/diagrams/layers.svg)

A write never overwrites blindly. The agent writes a new fact straight from its namespace and
parts. If the key already exists, the service refuses with the current value; the agent merges
the old value with the new truth and writes the full result with that revision. A stale
revision is refused the same way, so no update is lost. The service itself never calls an LLM.

![Create, then merge on conflict](docs/diagrams/read-write.svg)

Binding project policies are the user's call: adding, changing or removing one opens a
dialog in the MCP client (elicitation) with the exact text, and nothing is saved until the
user approves.

Reading a raw event or citing it in a new revision renews it. When retention has dropped it,
the revision still holds the real link, and the service returns it.

![Raw event retention](docs/diagrams/raw-lifecycle.svg)

## Install (Claude Code plugin)

```text
/plugin marketplace add NamTranQuoc/wn-project-memory
/plugin install agentic-memory
/agentic-memory:setup
```

`/plugin install` asks for two values:

| Value | Local mode | Remote mode |
| --- | --- | --- |
| MCP URL | keep the default `http://127.0.0.1:8765/mcp` | `https://<your host>/mcp` (https is required) |
| API key | created by setup in `~/.agentic-memory/.env`; paste it in `/plugin` → agentic-memory | the `MEMORY_API_KEY` of that service |

![Install and setup](docs/diagrams/install.svg)

- **Local:** setup downloads `compose.yml` from the GitHub release of the installed plugin
  version (image pinned by digest) and starts Postgres and the service with Docker, bound to
  `127.0.0.1` only. To update, run `/plugin update`, then setup again.
- **Remote:** run the same image anywhere behind TLS (see [Run the service](#run-the-service)).
- After setup, the skill takes over: in a new project it asks what the project is and which
  sources to register, proposes namespaces, and runs the first ingest. Updates of the skill
  come with `/plugin update`.

Other MCP clients (Cursor, …) can connect to the same URL with header `X-API-Key: <key>`;
`.cursor/skills/agentic-memory/SKILL.md` holds the same usage guide.

## Run the service

The service connects as its own database user, never as a Postgres superuser.
`bin/create-db-user.sh` prepares a database once, run by an admin: it creates the database,
its owner (the service's user), the `vector` and `pg_trgm` extensions, and the read-only
role of `query_deep_memory_sql`. Migrations then run as the service's user and create no roles.

```bash
# On an existing Postgres server (needs psql and an admin role):
ADMIN_URL=postgresql://postgres:<password>@db.example.com:5432/postgres bin/create-db-user.sh
# → prints DATABASE_URL / DATABASE_URL_SYNC for the new user (password shown once)
```

For development, `make db-up` starts Postgres in Docker and runs the same script on first
start (user `memory`, database `agentic_memory`, as in `.env.example`).

```bash
cp .env.example .env.local        # fill in real values there; .env.local is git-ignored
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'   # → MEMORY_API_KEY
make db-up && make sync && make migrate
make run                          # http://127.0.0.1:8000  (REST + /mcp)
```

With Docker only:

```bash
docker run -d -p 127.0.0.1:8765:8000 \
  -e DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/agentic_memory \
  -e DATABASE_URL_SYNC=postgresql+psycopg://USER:PASSWORD@HOST:5432/agentic_memory \
  -e MEMORY_API_KEY=... <dockerhub-user>/agentic-memory:latest
```

The container applies migrations on start. Behind a reverse proxy, add the public host name
to `MCP_ALLOWED_HOSTS`.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEMORY_API_KEY` | — | **Required**, ≥ 32 characters. The service does not start without it. |
| `DATABASE_URL`, `DATABASE_URL_SYNC` | local `agentic_memory` | Postgres with `pgvector` and `pg_trgm`, as the user from `bin/create-db-user.sh`. |
| `SQL_READER_ROLE` | `agentic_memory_sql_reader` | Role `query_deep_memory_sql` runs as; created by `bin/create-db-user.sh`. |
| `MAX_RAW_CHARS`, `MAX_TEXT_CHARS` | `200000`, `20000` | Largest raw payload; largest fact or note content. |
| `RAW_PAGE_CHARS`, `MAX_RAW_PAGE_CHARS` | `8000`, `20000` | Page size of `get_raw_context`. |
| `SEMANTIC_SEARCH_ENABLED` | `false` | `true` adds embeddings and the `semantic_search` tool. `false` makes no embedding call at all. |
| `EMBEDDING_DIRECT`, `EMBEDDING_API_BASE`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | Ollama `bge-m3`, 1024 | Embedding backend (Ollama direct or LiteLLM). Remote endpoints must be https. |
| `L4_IDLE_RETENTION_MONTHS` | `6` | Raw events live this long after their last touch. |
| `L4_TOUCH_MIN_DAYS` | `30` | A touch writes at most once per this many days. |
| `MCP_ALLOWED_HOSTS` | — | Extra Host names accepted by `/mcp`. |

After turning semantic search on, run `make embed-backfill` once.

## Tools

| Layer | MCP tools |
| --- | --- |
| L1 | `get_project_overview`, `set_project_overview`, `upsert_project_note`, `get_project_note`, `list_project_notes`, `get_active_policies`, `register_data_source`, `list_data_sources` |
| L2 | `upsert_namespace`, `list_namespaces`, `build_fact_key`, `list_fact_index` |
| L3 | `get_fact`, `write_fact`, `write_facts`, `get_fact_history`, `link_facts`, `search_facts`, `semantic_search` (only when enabled) |
| L4 | `log_raw_event`, `get_raw_context`, `query_deep_memory_sql`, `check_source_units`, `ingest_source_unit`, `get_source_unit`, `upsert_watermark`, `get_watermark`, `list_watermarks`, `reset_source_ingest` |
| Global | `find_person`, `list_people`, `upsert_person`, `upsert_identity`, `get_team`, `upsert_team`, `set_team_members`, `upsert_global_note`, `get_global_note`, `list_global_notes` |

REST mirrors them under `/l1`, `/l2`, `/l3`, `/l4` (with `?project_path=`) and `/global`.
Lists default to 5 rows, long text is cut at 1500 characters, raw payloads come in pages,
and SQL reads only the caller's own raw events with `LIMIT 10`. `get_project_overview`
returns the project, its policies and its namespaces in one call; search hits carry a snippet.

## Security

- One API key guards REST and `/mcp`; only `/health` and `/ready` are public.
- Policies need the user's approval through an MCP elicitation dialog the agent cannot
  answer. A policy saved through REST counts as approved by the person holding the key.
- Text read from memory is data, never instructions: the skill and the MCP instructions say
  so, and only approved policies are binding.
- Writes are size-limited (raw payload 200 000 characters, fact or note 20 000).
- The service uses its own database user; `query_deep_memory_sql` runs as a role that can
  read only `l4_raw_events`.
- The local stack listens on `127.0.0.1` only. A remote service must sit behind TLS.
- Secrets never enter memory: the skill and the MCP instructions tell the agent never to
  send one, and every write path masks secret-shaped values with regex as a second layer.
- `query_deep_memory_sql` may call only allow-listed functions and is checked against the
  query plan.
- Release images are pinned by digest in each release's `compose.yml`.
- `make hooks` installs a pre-commit hook that runs `make privacy-check`: it blocks secrets,
  real e-mail addresses, Teams/Jira links, home paths and any string from a local, git-ignored
  `.privacy-denylist`.

## Development

```bash
make test                                   # unit tests
# throwaway database on the dev Postgres (make db-up), prepared once:
docker compose exec -u postgres -e APP_DB_NAME=agentic_memory_test postgres \
  /docker-entrypoint-initdb.d/10-create-db-user.sh
make test-db TEST_DATABASE_URL=postgresql+asyncpg://memory:memory@127.0.0.1:5432/agentic_memory_test
make privacy-check
make diagrams                               # regenerate docs/diagrams/*.svg (archify)
```

## Release

Push a tag `vX.Y.Z` that matches `.claude-plugin/plugin.json`. The `release` workflow runs the
tests and the privacy check, builds a multi-arch image, pushes it to Docker Hub, and publishes a
GitHub release whose `compose.yml` pins that image by digest. It needs the repository secrets
`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` and the variable `DOCKERHUB_IMAGE`.
