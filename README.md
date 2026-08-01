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
cp .env.example .env   # Add your OPENAI_API_KEY
make migrate
```

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
In any local project where you want your AI to use this memory, run:
```bash
curl -sSL https://raw.githubusercontent.com/<YOUR_GITHUB_HANDLE>/<REPO_NAME>/main/memory_agent_skill.md -o .agent_memory_rules.md
```
Then instruct Cursor/Claude: *"Read `.agent_memory_rules.md` and follow the memory usage instructions."*
