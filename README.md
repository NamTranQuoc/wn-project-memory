# Agentic Memory Service MCP

A Hierarchical, Agentic Memory Service designed to attach to Claude Code or Cursor via the Model Context Protocol (MCP). Protects LLM context windows while providing deep, project-specific operational memory. Built for Enterprise scale with automated retention and resilient APIs.

## Features
- **4-Layer Architecture:** Working Memory (L1), Meta (L2), Distilled Semantic Rules (L3), and Raw Data Lake (L4).
- **High Performance & Auto-Maintenance:** Implements automated PostgreSQL Table Partitioning. Automatically drops data older than 6 months to save disk space.
- **Resilient Background Distillation:** Uses robust retry mechanisms (handling Rate Limits) to guarantee raw events are converted into searchable rules.
- **Hybrid Search:** pgvector + pg_trgm integration.
- **Context Window Protection:** Aggressive sanitization, truncation, and read limits.

## Prerequisites
- [uv](https://docs.astral.sh/uv/) installed
- Docker (for Postgres + pgvector)
- An OpenAI API key (used via LiteLLM for distillation + embeddings)

## Installation & Setup

### 1. Start Database
```bash
make db-up
```

### 2. Sync Dependencies & Configure
```bash
make sync
cp .env.example .env   # Add OPENAI_API_KEY; set MEMORY_API_KEY before any remote REST exposure
make migrate
```

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
   - `.env` in the memory repo has `LITELLM_API_BASE` / models set
   - I can run `make mcp` manually to smoke-test, but prefer editor-managed stdio via the MCP config you write

4) After wiring, follow the agentic-memory skill: on first use call `init_project_memory` with this project's absolute path, then use `search_memory` / `log_raw_event` / `update_working_memory` as appropriate.
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
   - POST   `{BASE}/projects/{project_path}/init`                 body: {"initial_context":"..."}
   - POST   `{BASE}/projects/{project_path}/events`               body: {"event_type":"...","content":"...","source_hash":null}
   - GET    `{BASE}/projects/{project_path}/search?query=...&search_type=hybrid&limit=5`
   - POST   `{BASE}/projects/{project_path}/sql`                  body: {"sql_query":"SELECT ..."}
   - GET    `{BASE}/projects/{project_path}/raw-events/{id}`
   - PATCH  `{BASE}/projects/{project_path}/working-memory`       body: {"current_focus_text":"..."}
   - GET    `{BASE}/projects/{project_path}/events/{id}/stream`   SSE distillation status
   - GET    `{BASE}/health` and `{BASE}/ready`

3) Create a small project helper the agent can reuse (prefer one file the skill can point at), e.g. `.claude/skills/agentic-memory/rest-client.md` or a tiny script, documenting:
   - BASE URL (from me)
   - MEMORY_API_KEY header requirement for non-health routes
   - that tool names in the skill map 1:1 to these REST endpoints
   - always pass this repo's absolute path as `project_path`
   - respect limits (search limit≤5, SQL auto LIMIT 10, truncated text → get raw event)

4) Do NOT configure MCP stdio for this mode. Operate only via REST + the skill rules (init → search before big changes → log_raw_event on feedback → update_working_memory for scratchpad).

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
