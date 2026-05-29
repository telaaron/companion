# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Tracking work that has landed on `main` but is not yet tagged.

## [1.1.4] — 2026-05-28

### Added
- Smart auto-scroll in chat: chat sticks to the bottom while streaming,
  pauses when the user scrolls up, and a floating "Jump to latest" button
  appears.

### Fixed
- Thirteen quick bugs from the v1.1.3 review pass: delete buttons in chat
  + projects + routines, voice mic visibility, audit + usage timestamp
  units, thinking-pane persistence, regenerate model picker, and more.

## [1.1.3] — 2026-05-27

### Added
- Build number (git SHA + UTC timestamp) shown in **Settings → Server**.

### Fixed
- Tauri updater progress callback handles the upstream `Option<u64>`
  total-size signature change.

## [1.1.2] — 2026-05-27

### Fixed
- `build_info.py` is now included in the PyInstaller bundle so the frozen
  binary reports its version on first launch.

## [1.1.1] — 2026-05-27

### Fixed
- Repaired the `build_info` import path inside the PyInstaller bundle.

## [1.1.0] — 2026-05-27

### Added
- **Full SvelteKit dashboard rewrite.** The vanilla `app.js` (~4,900 LOC)
  is replaced by a typed SvelteKit project under `web/` with one
  `.svelte` file per route, Svelte 5 runes for state, lucide icons.
- **Auto-updater.** Tauri pulls signed releases from GitHub and installs
  silently in the background on launch. Uses `tauri-plugin-updater` with
  minisign-signed manifests.
- **Project + model picker in chat header** — switches per session and
  persists through `PUT /v1/sessions/{id}`.
- **Voice recording** via `MediaRecorder` posting to `/v1/transcribe`.
- **Folder picker modal** shared by Projects + Settings.
- **Pinned memories** per project with CRUD UI.
- **Tool-block cards** with collapsible result preview, plus a click
  handler that opens the file-preview panel for `Read` calls.
- **Range pickers** on Usage + Insights (24h / 7d / 30d / custom).
- **Inline markdown** rendering for assistant replies with collapsible
  thinking-pane that survives stream end.
- **Cross-project file preview** — `session_id` resolves to project
  workspace; falls back to the `file_edits` allow-list when needed.

### Fixed
- Memory leak hardening in the indexer (skips `.git`, `node_modules`,
  `.venv`, build caches), the watchdog queue cap (5,000 items), and MCP
  stderr forwarding cap (4 MB / 60 s / server).
- PyInstaller dropped `_app/immutable/entry/*.js` because the directory
  name collided with internal names — switched to per-file `datas`
  tuples.
- `multiprocessing.freeze_support()` guard added so the frozen binary
  doesn't fork-bomb on macOS.
- Tauri WebView blank-screen on cold start: waits up to 60 s for the
  sidecar to bind and force-reloads after.
- HTML caching for `/ui/` disabled at the FastAPI layer so old bundle
  hashes don't get stuck in client caches.
- Chat SSE parser now reads Anthropic-format event frames correctly.

### Changed
- All UI now served from `web/build/`, mounted by FastAPI ahead of the
  legacy `ui_static/` bundle. SPA deep links resolved via a 404 fallback.

## [1.0.0] — 2026-05-25

### Added
- First public release.
- Tauri desktop bundle with PyInstaller-frozen FastAPI sidecar.
- Multi-OS GitHub Releases via CI matrix (macOS / Windows / Linux).
- Vanilla JS dashboard at `/ui/` with chat, projects, settings, env
  vault, usage, file edits, audit log, root files, skills, memory,
  routines, insights.
- Multi-provider model router (DeepSeek, OpenRouter, NVIDIA NIM, Z.ai,
  OpenAI-compatible, Anthropic).
- Background-safe job runner with SSE replay + tail.
- MCP server passthrough.
- File watcher + RAG indexer with FTS5.

[Unreleased]: https://github.com/telaaron/companion/compare/v1.1.4...HEAD
[1.1.4]: https://github.com/telaaron/companion/compare/v1.1.3...v1.1.4
[1.1.3]: https://github.com/telaaron/companion/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/telaaron/companion/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/telaaron/companion/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/telaaron/companion/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/telaaron/companion/releases/tag/v1.0.0
