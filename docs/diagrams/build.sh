#!/usr/bin/env bash
# Regenerate every diagram: archify JSON → checked HTML → dual-theme SVG for README.md.
# Needs Node >= 22, Chrome, and the archify skill (set ARCHIFY to its bin/archify.mjs).
set -euo pipefail
cd "$(dirname "$0")/../.."

ARCHIFY="${ARCHIFY:-$HOME/.claude/skills/archify/bin/archify.mjs}"
[ -f "$ARCHIFY" ] || { echo "archify not found; set ARCHIFY=/path/to/archify/bin/archify.mjs" >&2; exit 1; }

# name:type
DIAGRAMS="architecture:architecture layers:architecture read-write:sequence raw-lifecycle:lifecycle install:workflow"
stamp=$(date +%Y%m%d-%H%M%S)
html=()
for entry in $DIAGRAMS; do
  name="${entry%%:*}"; type="${entry##*:}"
  node "$ARCHIFY" finalize "$type" "docs/diagrams/$name.json" "docs/diagrams/$name.html" \
    --quality showcase --out-dir ".archify/$name-$stamp" --json >/dev/null \
    || { echo "✗ $name: see .archify/$name-$stamp" >&2; exit 1; }
  # Receipts hold absolute local paths; keep them out of the repo.
  mv "docs/diagrams/$name.delivery.json" ".archify/$name-$stamp/" 2>/dev/null || true
  echo "✓ $name.html"
  html+=("docs/diagrams/$name.html")
done
node docs/diagrams/export-svg.mjs "${html[@]}"
