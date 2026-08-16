# Agentic Memory Service MCP

A Hierarchical, Agentic Memory Service designed to attach to Claude Code or Cursor via the Model Context Protocol (MCP). Protects LLM context windows while providing deep, project-specific operational memory. Built for Enterprise scale with automated retention and resilient APIs.

## Features
- **Project-scoped memory:** every row is tied to a `projects` registry (`project_id` + denormalized `project_path`) so multiple projects never mix.
- **Layered knowledge store:** L0 working focus, L1 curated reference (incl. per-project policy), L2 rules/context, L3 distilled rules, L3-Ops typed ledgers, L4 raw event lake.
- **Data sources registry:** declare where facts come from (GitHub PR, Teams, Jira, local files, live `user_session`) and how to re-read them.
- **Incremental source-unit ledger:** durable per-unit dedup (`external_id` preferred, content hash fallback) so agents ingest only new/changed units without re-writing L4 duplicates.
- **Hybrid Search:** pgvector + pg_trgm on L3 rules and L3-Ops facts/tasks/refs.
- **Agent-driven writes:** the calling agent reads a raw event itself and writes structured L3/Ops rows directly (`upsert_fact` with stable `fact_key` / `upsert_task` / `upsert_watched_ref` / `upsert_distilled_rule`) — one business key = one current row; no automatic LLM distillation pass.
- **Context window protection:** sanitization, truncation (1500 chars), search/SQL hard limits.
- **Auto retention:** L4 monthly partitions; drop partitions older than 6 months.

## Architecture overview

### How pieces fit together

```mermaid
flowchart TB
  Agent["Claude Code / Cursor"]
  MCP["MCP stdio tools"]
  REST["FastAPI REST"]
  Svc["Services"]
  DB["Postgres + pgvector"]

  Agent --> MCP
  Agent --> REST
  MCP --> Svc
  REST --> Svc
  Svc --> DB
```

### Memory layers (read top → bottom for “what agent uses first”)

```mermaid
flowchart TB
  Live["Live SSOT\nTeams / GitHub / Jira / files / session"]

  subgraph project["One project — projects.project_path"]
    direction TB
    Reg["Registry\nprojects · sources\nconnection_config + read_recipe"]
    L0["L0 Working memory\nl0_working_memory\nsession focus only — not policy"]
    L1["L1 Curated references\nl1_references\nroster / protocol / is_policy=true"]
    L2["L2 Meta memory\nl2_meta_memory\nproject environment / structure"]
    L3["L3 Distilled knowledge\nl3_distilled_knowledge\natomic rules + hybrid search"]
    Ops["L3-Ops typed ledgers\nfacts · tasks · watermarks\nsource_units · watched_refs"]
    L4["L4 Raw event lake\nl4_raw_events\nmonthly partitions · drop after 6 months"]

    Reg --> L0 --> L1 --> L2 --> L3 --> Ops --> L4
  end

  Live -->|"agent fetches, then register / ingest"| Reg
  L4 -->|"agent writes structured rows"| L3
  L4 -->|"agent writes structured rows"| Ops
  Ops -.->|"source_id / raw_event_id / hashes"| L4
```

Live sources stay outside the database. The agent fetches them, registers `sources`, then writes down the stack: L4 raw payloads first, then structured L3 / L3-Ops rows that point back with `source_id` / `raw_event_id`. L0 is session scratch only. L4 partitions older than 6 months are dropped; `l3_source_units` keeps ingest identity so the agent does not re-write known units.

| Layer | Table(s) | Role | Typical ops |
| --- | --- | --- | --- |
| **Registry** | `projects`, `sources` | Identity + provenance. `sources` stores connection config + `read_recipe` to re-fetch live data. | `init_project_memory`, `register_data_source`, `list_data_sources` |
| **L0** | `l0_working_memory` | **Session focus only** (`current_focus_text`). Not for policies, rules, or anything a future session needs to recover. | `update_working_memory` |
| **L1** | `l1_references` | Curated reference docs, one row per named `ref_key` per project: rosters, seat commitments/DoD, source read-recipe guides, and **project-specific policy/workflow** (`is_policy=true` rows). | `upsert_l1_reference`, `get_l1_reference`, `list_l1_references`, `search_l1_references`, `get_active_policies` |
| **L2** | `l2_meta_memory` | Project rules, conventions, structure (stable *data* context) | set at `init_project_memory` |
| **L3** | `l3_distilled_knowledge` | Atomic distilled rules — **current state** keyed by `entity_key`; required `source_id` | `search_memory`, `upsert_distilled_rule`, get/list/delete by key |
| **L3-Ops** | `l3_watermarks`, `l3_source_units`, `l3_facts`, `l3_tasks`, `l3_watched_refs` | Typed operational ledgers (cursors, durable source-unit dedup, decisions/plans, open-loops, watched refs) | watermark / source-unit / fact / task / watched-ref tools |
| **L4** | `l4_raw_events` (partitioned) | Append-only raw payloads; 6-month retention | `log_raw_event`, `get_raw_context`, `query_deep_memory_sql` |
| **Policy / workflow** | **L1** (`is_policy=true` rows), loaded every session via `get_active_policies` | Project-specific write-gates, phase rules, escalation contacts — one generic consumer skill loads whichever project's policy applies by switching `project_path`. The consumer skill still enforces the universal safety floor (write-class confirmation, no destructive action without approval) that stored policy can never relax. | `get_active_policies`, `upsert_l1_reference(..., is_policy=true)` |

### Policy & workflow — not L0

**Do not store policies or workflows in L0.** L0 is a single overwriteable scratchpad per project. Putting “confirm before post”, hard-stops, or sweep procedures there mixes them with session focus and they get wiped on the next `update_working_memory`. Project-specific policy/workflow belongs in **L1** instead (`is_policy=true` rows) — see the table above.

| Concern | Where it lives | xora-dev equivalent |
| --- | --- | --- |
| Executable policy (grounded-or-silent, write-gate, hard-stops, phase-aware claiming, coverage footer) | **L1** `is_policy=true` rows (loaded via `get_active_policies` every session) + the consumer skill's retained universal safety floor | `xora-dev/SKILL.md` §§1,3,7–9 |
| Stable cited playbooks (comment grammar, roster, seat DoD, read recipes as *data*) | **L1** (plain reference rows) | `references/PROTOCOL.md`, `ROSTER.md`, `MY-WORK.md`, `SOURCES.md` |
| Fast-moving ledgers | L3-Ops + L4 | `state/*` |
| “What am I doing *this* session?” | L0 only | (no durable file; optional one-liner) |

**Update rules** (same discipline as xora-dev writing to `references/` / `state/`):

1. **Live source wins.** PR body / comment / Teams / board is SSOT; memory and skill text are caches.
2. **Correct in place.** When a live ruling contradicts L1/L2/L3/Ops or a skill-noted fact, overwrite the current value — do not append “previously we thought…”.
3. **Cite or don’t claim.** Every durable rule/fact must keep provenance (`source_id` / `raw_event_id` / URL in content). Unverifiable → re-fetch or drop.
4. **Policy edits are deliberate.** Changing a project's write-gates/hard-stops/sweep order means calling `upsert_l1_reference` on the relevant `is_policy=true` row — always show the exact resulting text and get explicit confirmation first, every time. Plain (non-policy) L1 references can be corrected in place freely. Never put policy in L0.
5. **Watermarks & tasks:** advance cursors only for what was actually read; soft-close tasks when the live source closes the loop (same honesty as xora-dev §6).

```mermaid
flowchart LR
  L1Policy["L1\nis_policy=true rows"]
  Skill["Consumer skill\nsafety floor"]
  L1L2L3["L1 (plain) / L2 / L3\nreference + rule data"]
  Ops["L3-Ops + L4\nledgers"]
  L0["L0\nsession focus only"]
  Live["Live SSOT\nPR / Teams / board"]

  Live --> L1Policy
  Live --> L1L2L3
  Live --> Ops
  L1Policy -->|"get_active_policies, every session"| Skill
  Skill -->|"enforces safety floor on top of"| L1Policy
  Skill -->|"instructs agent"| Ops
  Skill -->|"instructs agent"| L1L2L3
  L0 -.->|"never holds policy"| Skill
```

**Reference chain:** upper tiers point downward so the agent can jump from a compact Ops/L3 row to the raw event and to the source’s `read_recipe` when content may be stale (`source_id`, `raw_event_id`, dual hashes).

### L3-Ops tables (typed siblings)

| Table | Mirrors (e.g. war-room skill) | Notes |
| --- | --- | --- |
| `l3_watermarks` | incremental cursors | Structured JSON cursors; **no** embedding; ordered by `checked_at`. Advance only after successful ingest. |
| `l3_source_units` | durable ingest ledger | Per-unit identity (`external_id` → `item_key`, else content-hash key); stores `content_hash` + native `source_hash`; survives L4 retention |
| `l3_facts` | journal / decisions | Stable `fact_key` unique per project; one current row; `kind`: `fact` \| `decision` \| `plan` \| `question` \| `issue` \| `solution`; required `source_id` |
| `l3_tasks` | open-loops | Stable `task_key` (e.g. `O-28`); `open` / `partial` / `closed` |
| `l3_watched_refs` | watched refs | PR / issue / SHA / path / ticket / tag; disposition `mine` \| `queued` \| `resolved`; `why` (tracking reason) and `status_note` (latest state) update independently |

### Main activity flows

**1. Bootstrap a project**

```mermaid
sequenceDiagram
  participant A as Agent
  participant U as User
  participant S as Service
  participant DB as Postgres
  A->>S: list_data_sources / get_active_policies
  alt first session
    A->>U: ask context, sources, policy rules
    A->>S: init_project_memory(path, context, sources?)
    S->>DB: upsert projects + L0/L2 + sources
  else already initialized
    A->>S: get_active_policies
  end
  S-->>A: project_id + status / policies
```

**2. Ingest → agent reads → agent writes structured rows**

```mermaid
flowchart LR
  Live["Agent fetches live\nTeams/Git/gh"] --> Check["check_source_units"]
  Check -->|"unknown/changed"| Ingest["ingest_source_unit"]
  Check -->|unchanged| Stop["stop at known boundary"]
  Ingest --> Ledger["l3_source_units"]
  Ingest -->|"created/changed"| L4["L4 raw_events"]
  Ingest -->|unchanged| Ledger
  L4 --> AgentWrite["Agent upserts\nfact/task/ref/rule"]
  AgentWrite --> WM["upsert_watermark"]
```

There is no background or automatic LLM distillation step. Prefer `ingest_source_unit` for crawled sources (idempotent ledger). Use `log_raw_event` for `user_session` / ad-hoc audit only. The agent that read the source decides what is atomic and writes it directly, citing `raw_event_id`.

**3. Day-to-day agent loop**

1. `search_memory` / `search_facts` / `list_tasks` / L1 search — cheap context **before** live API calls  
2. Only if memory is insufficient or user demands freshness: work against live tools using each source’s `read_recipe`  
3. `check_source_units` → `ingest_source_unit` for new/changed units → structured upserts with provenance  
4. `upsert_watermark` after successful incremental reads (never after 429/failure)  
5. When the user mentions a new source → `register_data_source` + initial ingest for that source only  
6. `update_working_memory` for the current session focus  
7. If a search hit is truncated → `get_raw_context(raw_event_id)`

**4. First-time full ingest & full reindex** (agent-driven; service does not call gh/Teams itself)

Defined in the agentic-memory skill § *Full ingest & reindex*:

- **Full ingest:** after init, for each active source except `user_session` → execute `read_recipe` → `ingest_source_unit` + Ops upserts → `upsert_watermark` (honest `indexed_through` vs `full_read_ids`) → coverage footer.
- **Incremental (default):** watermark + source-unit ledger → newest-first page → stop at known unchanged boundary; Git compares native hashes before reading bodies.
- **Full reindex:** if the user grants **session write authorization**, run `preview_external_reindex` → `apply_external_reindex_reset(confirm=true)` → cold crawl → upsert → legacy reconcile without pausing for per-write asks (external sources only; never `user_session` / `local_file` / `legacy_unattributed`). Without that grant, show preview/map and wait for approval. Never call `init_project_memory` during reindex (it overwrites L2).

### Tool surface (MCP ↔ REST)

| Concern | MCP tools | REST (under `/projects/{project_path}/…`) |
| --- | --- | --- |
| Bootstrap / focus | `init_project_memory`, `update_working_memory` | `POST …/init`, `PATCH …/working-memory` |
| Sources | `register_data_source`, `list_data_sources` | `POST …/sources`, `GET …/sources` |
| Source units (incremental) | `ingest_source_unit`, `check_source_units`, `get_source_unit` | `POST …/source-units/ingest`, `POST …/source-units/check`, `GET …/source-units` |
| Raw events | `log_raw_event`, `get_raw_context`, `query_deep_memory_sql` | `POST …/events`, `GET …/raw-events/{id}`, `POST …/sql` |
| L1 references / policy | `upsert_l1_reference`, `get_l1_reference`, `list_l1_references`, `search_l1_references`, `get_active_policies` | `POST/GET …/l1-references`, `GET …/l1-references/{ref_key}`, `GET …/l1-references/search`, `GET …/l1-references/policies` |
| L3 rules | `search_memory`, `upsert_distilled_rule`, `get_distilled_rule`, `list_distilled_rules`, `delete_distilled_rule` | `GET …/search`, `POST/GET …/rules`, `GET/DELETE …/rules/{entity_key}` |
| Watermarks | `upsert_watermark`, `get_watermark`, `list_watermarks` | `PUT …/watermarks`, `GET …/watermarks`, `GET …/watermarks/{source_key}` |
| Facts | `upsert_fact`, `get_fact`, `list_facts`, `search_facts`, `delete_fact` | `POST/GET …/facts`, `GET …/facts/search`, `GET/DELETE …/facts/{fact_key}` |
| Tasks | `upsert_task`, `close_task`, `list_tasks` | `POST …/tasks`, `POST …/tasks/{key}/close`, `GET …/tasks` |
| Watched refs | `upsert_watched_ref`, `list_watched_refs` | `POST …/watched-refs`, `GET …/watched-refs` |
| External reindex | `preview_external_reindex`, `apply_external_reindex_reset`, `inventory_legacy_state` | `POST …/reindex/preview`, `POST …/reindex/apply`, `GET …/legacy` |

Callers always pass **`project_path`** (absolute path of the consumer project). The service resolves/creates `project_id` internally.

## Prerequisites
- [uv](https://docs.astral.sh/uv/) installed
- Docker (for Postgres + pgvector)
- An embedding backend (see below) — default local setup uses Ollama `bge-m3` (1024-dim)

## Installation & Setup

### 1. Start Database
```bash
make db-up
```

### 2. Sync Dependencies & Configure
```bash
make sync
cp .env.example .env   # Configure embedding; set MEMORY_API_KEY before any remote REST exposure
make migrate
```

**Embedding config** (from `.env` / `.env.example`):

| Mode | Key settings |
| --- | --- |
| **Direct Ollama** (bypass LiteLLM) | `EMBEDDING_DIRECT=true`, `EMBEDDING_API_BASE=http://localhost:11434`, `EMBEDDING_MODEL=ollama/bge-m3`, `EMBEDDING_DIMENSIONS=1024` |
| **Via LiteLLM** (default in `.env.example`) | `EMBEDDING_DIRECT=false`, `LITELLM_API_BASE=…`, same model vars; or OpenAI: `OPENAI_API_KEY=sk-…`, `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIMENSIONS=1536` |

When `EMBEDDING_DIRECT=true`, the service calls Ollama native `POST /api/embeddings` (provider prefix before the first `/` is stripped, e.g. `ollama/bge-m3` → `bge-m3`). Base URL resolve order: `EMBEDDING_API_BASE` → `LITELLM_API_BASE` → `http://localhost:11434`. `http` base URLs are only allowed on loopback; remote endpoints must use `https`.

Changing `EMBEDDING_DIMENSIONS` requires a matching DB vector column size (see Alembic migrations) — do not mix spaces from different models without re-embedding.

**Security notes (REST vs MCP):**
- **MCP stdio** (`make mcp`) is a local process trust boundary — no network listener.
- **REST** has no user/multi-tenant model beyond `project_path`. Before binding beyond localhost (`APP_HOST=0.0.0.0` or a public reverse proxy), set a strong `MEMORY_API_KEY` and send it as `X-API-Key` or `Authorization: Bearer`. Prefer TLS termination (mTLS/OAuth at the proxy) for production.

### 3. Run Server
```bash
make run
```
*(The background scheduler will automatically bootstrap your initial database partitions upon startup).*

### 4. Run MCP Server (stdio)
```bash
make mcp
```

### Useful Make Targets
| Target | Description |
|--------|-------------|
| `make sync` | Install/sync Python deps with uv |
| `make run` | Start FastAPI with uvicorn (reload) |
| `make mcp` | Start MCP stdio server |
| `make migrate` | Run Alembic migrations |
| `make db-up` / `make db-down` | Start/stop Postgres+pgvector |
| `make build` | Build the app Docker image |
| `make test` | Run pytest |

## Integration

### A) Copy-paste prompts for your agent (Claude / Cursor)

Paste **one** of the prompts below into Claude Code or Cursor. The agent should do the wiring for you — do not configure MCP/REST manually unless the agent asks for a path or URL you must fill in.

#### Prompt 1 — Local MCP (stdio) on this machine

```text
Connect this project to the local Hierarchical Agentic Memory Service via MCP (stdio).

Do all of the following for me (edit files yourself; ask me only if a path is missing):

1) Install the agentic-memory skill into THIS consumer project:
   - Claude Code: create `.claude/skills/agentic-memory/SKILL.md`
   - Cursor: create `.cursor/skills/agentic-memory/SKILL.md`
   Source of truth (fetch if needed):
   https://raw.githubusercontent.com/NamTranQuoc/wn-project-memory/main/.claude/skills/agentic-memory/SKILL.md
   (Cursor may use the same content under `.cursor/skills/agentic-memory/SKILL.md`)

2) Register an MCP server named `agentic-memory` that runs stdio against the local memory repo.
   Absolute path to the memory service repo (replace if different on my machine):
   `/Users/namtran/personal/project/wn-project-memory`
   Command:
   - cwd: that repo path
   - command: `uv`
   - args: `["run", "python", "-m", "src.mcp_server"]`
   Wire this into the correct editor config (Cursor MCP settings / Claude Code MCP config). Do not start a long-lived process in the chat; just write the config so the editor can spawn stdio MCP.

3) Prerequisites I already handle outside the editor (mention if missing, do not invent secrets):
   - Postgres for the memory service is up
   - `.env` in the memory repo has embedding configured (`EMBEDDING_DIRECT` / `EMBEDDING_API_BASE` or LiteLLM + models)
   - I can run `make mcp` manually to smoke-test, but prefer editor-managed stdio via the MCP config you write

4) After wiring, follow the agentic-memory skill:
   - First session: ask me briefly for project context, context sources (Teams/GitHub/Jira/…), the local plan/doc folder (default `~/Desktop/memory/{repo-name}/{plan,doc}/`, or the path I name), and any policy rules I want stored; register that folder as `local_plans`; then call `init_project_memory` with this project's absolute path and optional sources, then `get_active_policies` (treat any result as binding).
   - Every session: memory-first — `search_memory` / `search_facts` / L1 search before live Teams/Git greps.
   - When I mention a new source, `register_data_source` and initial-ingest that source only.
   - External crawl: `check_source_units` → `ingest_source_unit` → structured upserts with `raw_event_id`/`source_hash` → `upsert_watermark` only after success.
   - Use `log_raw_event` for live `user_session` decisions; use `update_working_memory` for scratchpad only.
```

#### Prompt 2 — Remote / server via REST (no local MCP stdio)

```text
Connect this project to the Hierarchical Agentic Memory Service over REST (service runs on a server; no MCP stdio on my laptop).

Do all of the following for me:

1) Install the agentic-memory skill into THIS consumer project (same as local):
   - Claude: `.claude/skills/agentic-memory/SKILL.md`
   - Cursor: `.cursor/skills/agentic-memory/SKILL.md`
   Fetch from:
   https://raw.githubusercontent.com/NamTranQuoc/wn-project-memory/main/.claude/skills/agentic-memory/SKILL.md

2) Treat the memory tools as HTTP calls to the FastAPI base URL I give you (ask me once if unknown).
   Default local example: `http://localhost:8000`
   Production MUST be HTTPS behind a reverse proxy (or equivalent TLS), with MEMORY_API_KEY set on the server.
   Ask me for MEMORY_API_KEY once; send it on every memory call as header `X-API-Key: <key>`
   (or `Authorization: Bearer <key>`). Never invent a key. `/health` and `/ready` may be unauthenticated.
   Base path patterns (URL-encode project_path when it contains slashes):
   - POST   `{BASE}/projects/{project_path}/init`                 body: {"initial_context":"...","sources":[...]}
   - POST   `{BASE}/projects/{project_path}/events`               body: {"event_type":"...","content":"...","source_key":null}
   - POST   `{BASE}/projects/{project_path}/source-units/ingest`  body: {"source_key":"...","content":"...","external_id":null,"source_hash":null,"stream_key":"messages"}
   - POST   `{BASE}/projects/{project_path}/source-units/check`   body: {"source_key":"...","candidates":[...],"limit":5}
   - GET    `{BASE}/projects/{project_path}/source-units?source_key=...&external_id=...`
   - GET    `{BASE}/projects/{project_path}/search?query=...&search_type=hybrid&limit=5`
   - POST   `{BASE}/projects/{project_path}/sql`                  body: {"sql_query":"SELECT ..."}
   - GET    `{BASE}/projects/{project_path}/raw-events/{id}`
   - PATCH  `{BASE}/projects/{project_path}/working-memory`       body: {"current_focus_text":"..."}
   - POST/GET `{BASE}/projects/{project_path}/sources`
   - PUT/GET  `{BASE}/projects/{project_path}/watermarks`
   - POST/GET `{BASE}/projects/{project_path}/facts` (+ `…/facts/search`, `…/facts/{fact_key}`)  body requires `fact_key`; may include `raw_event_id`, `source_hash`
   - POST/GET `{BASE}/projects/{project_path}/reindex/preview` and `…/reindex/apply` (confirm=true); `GET …/legacy`
   - POST/GET `{BASE}/projects/{project_path}/tasks` (+ `…/tasks/{key}/close`)
   - POST/GET `{BASE}/projects/{project_path}/watched-refs`        body accepts optional `status_note`, `raw_event_id`, `source_hash`
   - POST/GET `{BASE}/projects/{project_path}/l1-references` (+ `…/l1-references/{ref_key}`, `…/l1-references/search`, `…/l1-references/policies`)
   - GET    `{BASE}/health` and `{BASE}/ready`

3) Create a small project helper the agent can reuse (prefer one file the skill can point at), e.g. `.claude/skills/agentic-memory/rest-client.md` or a tiny script, documenting:
   - BASE URL (from me)
   - MEMORY_API_KEY header requirement for non-health routes
   - that tool names in the skill map 1:1 to these REST endpoints
   - always pass this repo's absolute path as `project_path`
   - respect limits (search limit≤5, SQL auto LIMIT 10, truncated text → get raw event)
   - memory-first + incremental source-unit ingest (do not re-grep Teams when memory can answer)

4) Do NOT configure MCP stdio for this mode. Operate only via REST + the skill rules (ask setup questions on first init including the local plan/doc folder / `local_plans` → get_active_policies → memory search before live APIs → ingest_source_unit for crawls → register newly mentioned sources → update_working_memory for scratchpad).

5) Smoke-check with GET `{BASE}/health`. If it fails, tell me the service is down; do not invent a working connection.
```

### B) Optional: fetch skill only (if you still want a one-liner)

**Claude Code**
```bash
mkdir -p .claude/skills/agentic-memory
curl -sSL https://raw.githubusercontent.com/NamTranQuoc/wn-project-memory/main/.claude/skills/agentic-memory/SKILL.md \
  -o .claude/skills/agentic-memory/SKILL.md
```

**Cursor**
```bash
mkdir -p .cursor/skills/agentic-memory
curl -sSL https://raw.githubusercontent.com/NamTranQuoc/wn-project-memory/main/.cursor/skills/agentic-memory/SKILL.md \
  -o .cursor/skills/agentic-memory/SKILL.md
```
