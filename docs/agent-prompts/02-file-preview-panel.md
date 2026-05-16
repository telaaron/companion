# 1.3 — File preview side panel

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

When the agent reads or writes a file, the dashboard opens a collapsible
right-rail panel showing the file's current contents with syntax
highlighting — Claude Code's "you just modified this" view.

## Files

- `api/dashboard_routes.py` — new `GET /v1/preview/file?path=<>&session_id=<>`
  returning `{content, language, size, truncated}`. Path resolved through
  `Workspace.resolve(...)` so traversal is blocked. Refuse > 1 MB.
- `api/ui_static/app.js` —
  - `openPreviewPanel(path, sessionId)` injects `<aside class="preview">`
    next to `.messages`.
  - Wire every Read/Write/Edit tool block's rendered title to a click
    handler that opens the panel.
  - Panel tabs: **Recent reads**, **Recent writes**, **Current file**.
- `api/ui_static/styles.css` — `.preview` panel: sticky header, language
  badge, line-numbered `<pre>`, copy button. Fits next to `.messages` in
  the chat layout; collapses on viewports < 1100 px.
- Vendor [`prism.js`](https://prismjs.com/) + `prism-tomorrow.css` for
  syntax highlighting (vanilla, no build step). Put in
  `api/ui_static/vendor/prism/`.
- `tests/api/test_preview_endpoint.py` (new) — path-traversal denial +
  size-limit refusal.

## Implementation plan

1. Add `GET /v1/preview/file` endpoint. Validate `path` via the existing
   `Workspace` resolver (same one Read/Write tools use). On traversal,
   return 403. On size > 1 MB, return 413 with `{"truncated": true}`.
   Language hint from file extension via a small map.
2. Vendor Prism (core + line-numbers plugin + common languages: js, ts,
   py, json, yaml, md, html, css, sql, rust, go). Include from
   `index.html`.
3. In `app.js`, register a global registry of recent file paths per
   session (LRU, max 20). Push on every Read/Write/Edit tool block as
   it streams.
4. Add `openPreviewPanel(path, sessionId)`. Fetches the endpoint, renders
   into a sliding panel docked right of `.messages`. Sticky header with
   path + copy + close buttons. Tabs switch between Recent reads / writes
   / current file.
5. Style: panel is `width: clamp(320px, 32vw, 520px)`, slides in with a
   140 ms ease, scroll within is independent of the chat scroll.
6. Add tests: GET with a path outside workspace → 403. GET with a 2 MB
   file → 413. GET with a normal file → 200 + correct language hint.

## Acceptance

- Chat: "edit my README.md and add a section about X." Side panel opens,
  shows the updated README with syntax highlighting, scroll position
  remembered.
- Click any past Read tool block in the transcript → opens the file as
  it was last read.
- Mobile / narrow window: panel collapses behind a button, doesn't break
  layout.

## Risks

- Path security is the high-risk surface. Always go through `Workspace.resolve`.
- Prism is ~50 KB gzipped — fine for dashboard but lazy-load on first
  preview open.

## Verify

```bash
uv run ruff format --check && uv run ruff check && uv run ty check
uv run pytest tests/api/test_preview_endpoint.py -v
uv run pytest -v --tb=short
```
