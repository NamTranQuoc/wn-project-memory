#!/usr/bin/env bash
# Start (or update) the local agentic-memory stack with Docker.
# Downloads compose.yml from the GitHub release that matches the installed plugin version
# (its image is pinned by digest), copies create-db-user.sh next to it (the database's init
# step: the service connects as its own user, not the Postgres superuser), creates
# ~/.agentic-memory/.env with random secrets on first run, then waits for /health. After /plugin update, re-running it moves the stack
# to the new version.
set -euo pipefail

HOME_DIR="${AGENTIC_MEMORY_HOME:-$HOME/.agentic-memory}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PORT="${MEMORY_PORT:-8765}"

fail() { echo "✗ $*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker not found. Install Docker Desktop, then run /agentic-memory:setup again."
docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop, then run /agentic-memory:setup again."
docker compose version >/dev/null 2>&1 || fail "docker compose v2 is required."
command -v python3 >/dev/null || fail "python3 is required to create secrets."

read -r REPO_URL VERSION < <(python3 -c \
  'import json,sys; m=json.load(open(sys.argv[1])); print(m["repository"], m["version"])' \
  "$PLUGIN_ROOT/.claude-plugin/plugin.json")
RELEASE_URL="$REPO_URL/releases/download/v$VERSION"
mkdir -p "$HOME_DIR"
chmod 700 "$HOME_DIR"

echo "→ compose.yml from $RELEASE_URL"
curl -fsSL "$RELEASE_URL/compose.yml" -o "$HOME_DIR/compose.yml.new" \
  || fail "could not download compose.yml of release v$VERSION"
mv "$HOME_DIR/compose.yml.new" "$HOME_DIR/compose.yml"
install -m 755 "$PLUGIN_ROOT/bin/create-db-user.sh" "$HOME_DIR/create-db-user.sh"

if [ ! -f "$HOME_DIR/.env" ]; then
  umask 077
  python3 - "$HOME_DIR/.env" "$PORT" <<'PY'
import secrets, sys
path, port = sys.argv[1], sys.argv[2]
with open(path, "w") as fh:
    fh.write(f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}\n")
    fh.write(f"MEMORY_DB_PASSWORD={secrets.token_urlsafe(24)}\n")
    fh.write(f"MEMORY_API_KEY={secrets.token_urlsafe(32)}\n")
    fh.write(f"MEMORY_PORT={port}\n")
PY
  NEW_KEY=1
  echo "→ created $HOME_DIR/.env with new database passwords and a MEMORY_API_KEY"
else
  NEW_KEY=0
  echo "→ keeping existing $HOME_DIR/.env"
fi
PORT=$(grep -E '^MEMORY_PORT=' "$HOME_DIR/.env" | cut -d= -f2 || true)
PORT="${PORT:-8765}"

cd "$HOME_DIR"
docker compose --env-file .env -f compose.yml pull
docker compose --env-file .env -f compose.yml up -d

echo -n "→ waiting for http://127.0.0.1:$PORT/health "
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "✓"
    echo "✓ agentic-memory is running on http://127.0.0.1:$PORT/mcp"
    if [ "$NEW_KEY" = 1 ]; then
      echo "! Copy MEMORY_API_KEY from $HOME_DIR/.env into /plugin → agentic-memory → api_key,"
      echo "  then run /mcp to reconnect. The key is not printed here on purpose."
    fi
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo
docker compose --env-file .env -f compose.yml logs --tail 40 app >&2 || true
fail "service did not become healthy"
