---
description: Connect agentic-memory — start the local Docker stack or check a hosted service.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/setup-local.sh), Bash(curl -fsS *)
---

Set up the agentic-memory MCP connection. The configured MCP URL is `${user_config.mcp_url}`.

1. Choose the mode from that URL.
   - Host is `127.0.0.1`, `localhost` or `[::1]` → **local**.
   - Anything else → **remote**.

2. **Local.** Run `${CLAUDE_PLUGIN_ROOT}/bin/setup-local.sh` and show its output.
   - It needs Docker. If it fails, report the exact error line and stop.
   - On the first run it creates `~/.agentic-memory/.env` with a new `MEMORY_API_KEY`.
     Tell the user to copy that value into `/plugin` → agentic-memory → `api_key`, then
     run `/mcp` to reconnect. Do not read or print the key yourself.
   - After `/plugin update`, running this command again moves the stack to the new version.

3. **Remote.** The URL must start with `https://`. If it starts with `http://`, stop and
   tell the user that a remote memory service must use https.
   Check `curl -fsS <origin of the URL>/health`. A failure means the service is down or
   the URL is wrong; report it and stop.

4. Confirm the MCP connection: call the `get_project_overview` tool of the
   `agentic-memory` server with `project_path` set to the absolute path of the current repo.
   - `not_found` means the connection works and this project is new → follow the
     agentic-memory skill, section "Init".
   - Unauthorized or no such tool → the API key is missing or wrong. Ask the user to fix
     `api_key` in `/plugin` and run `/mcp`.
