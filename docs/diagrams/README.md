# Diagrams

Sources for the pictures in the main README. Edit the JSON, then run `make diagrams`.

| File | Type | Shows |
| --- | --- | --- |
| `architecture.json` | architecture | Plugin → API key gate → MCP/REST → services → Postgres |
| `layers.json` | architecture | L1 → L2 → L3 → L4 and the global directory |
| `read-write.json` | sequence | Build a key, read a fact, merge, write with `base_revision` |
| `raw-lifecycle.json` | lifecycle | Raw event retention by last touch |
| `install.json` | workflow | Plugin install and `/agentic-memory:setup` |

`build.sh` runs [archify](https://github.com/tt-a1i/archify) `finalize` for each JSON (schema,
layout and browser checks), keeps the checked `*.html` (open it for the interactive view), and
exports `*.svg` with `export-svg.mjs`, the same dual-theme file as the viewer's Export → SVG.
Receipts go to `.archify/` (git-ignored) because they contain local paths.
