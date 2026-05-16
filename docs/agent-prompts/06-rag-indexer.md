# 2.1 — Memory + RAG over notes / vault / files

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

The AI can search across:
- Past chat turns (already indexed in `memory_fts`).
- All files under `AGENT_DEFAULT_WORKSPACE` (chunked + indexed on first
  scan; refreshed by a filesystem watcher).
- Every Obsidian vault registered as a plugin.

When the user asks *"remind me what I wrote about pricing last month"*,
the agent calls `Search(query="pricing")` and quotes the matched source.

## Files

- `api/agent/indexer.py` (new) — scans configured directories on startup,
  watches via `watchdog`, chunks files (~800 token windows), writes into
  `memory_fts` with `kind="file"` and `path` metadata.
- `api/agent/extras/search.py` — already exists. Extend the tool schema
  with optional `path_prefix` and `kind` filters (the underlying datastore
  call already supports them).
- `api/dashboard_routes.py` — `POST /v1/index/rescan` triggers a full
  rescan; `GET /v1/index/status` returns index size, last scan timestamp,
  per-source counts.
- `api/ui_static/app.js` — new "Memory" page module showing index status,
  top tags, last scan time, "Rescan now" button.
- `config/settings.py` — `MEMORY_INDEX_PATHS` (comma-separated abs paths),
  `MEMORY_INDEX_MAX_BYTES` (default 500_000_000), `MEMORY_INDEX_EXTS`
  (default `.md,.txt,.py,.ts,.tsx,.js,.json,.yaml,.yml,.toml,.rst`).
- `pyproject.toml` — add `watchdog` to dependencies.
- `tests/agent/test_indexer.py` (new) — scan a tmp dir, query, expect
  hits; modify a file, observe debounced reindex; respects max_bytes.

## Implementation plan

1. `Indexer` class with `scan(paths)`, `start_watch(paths)`,
   `stop_watch()`. Uses async queue + a worker that pulls (path, action)
   tuples and writes to `memory_fts`.
2. Chunking: walk file, split on paragraph boundaries, accumulate ~800
   tokens per chunk (use `tiktoken` cl100k_base for the count). Each
   chunk row carries `path`, `chunk_idx`, `kind="file"`.
3. Watcher: `watchdog.observers.Observer` with a debounced handler
   (250 ms) so saving a file doesn't trigger 10 reindexes.
4. Startup wiring in `runtime.py::startup`: if
   `Settings.memory_index_paths` is non-empty, start a background task
   that runs `Indexer.scan(...)` then `Indexer.start_watch(...)`. Log
   `INDEXER: scanned {count} files in {duration:.1f} s`.
5. Stop in `shutdown`.
6. UI: "Memory" page polls `/v1/index/status` every 10 s. Rescan button
   disabled while a scan is in flight.

## Acceptance

1. Set `MEMORY_INDEX_PATHS=~/Documents/Obsidian Vault,~/projects`,
   restart. Log line `INDEXER: scanned 1247 files in 8.4 s` appears.
2. Chat: *"find my notes mentioning Vercel cold starts"*. The agent calls
   `Search(query="Vercel cold starts")`, gets ≥1 hit, quotes the most
   relevant one back.
3. Touch a file in a watched dir → within 5 s the index reflects the
   change (verify via `GET /v1/index/status` last_scan_ms).
4. Tests pass.

## Risks

- Disk size: FTS5 over 100k files can exceed 500 MB. Cap with
  `MEMORY_INDEX_MAX_BYTES` and skip files over 256 KB.
- `watchdog` on macOS needs `fsevents` extras; package as
  `watchdog[watchmedo]`.
- Embedding-free FTS misses semantic hits. Out of scope for v1; add as
  Phase 2.1.b.

## Verify

```bash
uv run pytest tests/agent/test_indexer.py -v
uv run pytest -v --tb=short
```
