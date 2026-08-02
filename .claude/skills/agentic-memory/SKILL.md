---
name: agentic-memory
description: >
  Use Hierarchical Project Memory via MCP for project-specific architectural
  decisions, conventions, and operational context. Apply when entering a project,
  before structural changes, when learning from feedback/bug fixes, or when
  truncated memory results need full raw context.
---

# AGENTIC MEMORY SKILL INTEGRATION

You are connected to a Hierarchical Project Memory via MCP. This memory tracks architectural decisions, conventions, and context specific to this local project path.

**YOUR OPERATING RULES:**
0. **Bootstrapping (First Action):** When you enter this project, if you do not know the project's meta context, immediately call `init_project_memory` to initialize the database for this path.
1. **Always Check Context First:** Before making structural changes or writing large features, call `search_memory` (try `hybrid` search_type) to check for established rules.
2. **Handle Truncated Outputs:** If a search result ends with `... [truncated]`, use `get_raw_context` with the provided `raw_event_id` to retrieve the rest.
3. **Optimized SQL Queries:** When calling `query_deep_memory_sql`, ALWAYS include a `created_at` time-range. The backend drops data older than 6 months.
4. **Continuous Learning:** Upon receiving feedback or bug fixes, call `log_raw_event`.
5. **Working Memory:** Use `update_working_memory` to leave scratchpad notes.
