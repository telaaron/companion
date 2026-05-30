# BUGS.md — Aktueller Bug-Tracker

> Aaron füllt hier neue Bugs ein. KIs lesen das als erstes.
> Für Kontext: [CLAUDE.md](CLAUDE.md) — alles was du über die Codebase wissen musst.

---

## Format fuer neuen Bug

```markdown
### BUG-XXX: Kurzer Titel
**Status**: offen | in Arbeit | gefixt
**Symptom**: Was passiert konkret?
**Reproduzieren**: Wie genau?
**Erwartet**: Was sollte passieren?
**Dateien**: Welche Dateien sind wahrscheinlich betroffen?
**Notizen**: Zusaetzlicher Kontext
```

---

## Offene Bugs

_(none — alle bekannten Bugs gefixt 2026-05-25)_

---

## Gefixte Bugs

### BUG-F13: Chat-UI — Streams, Delete, Live-Formatierung, Parallel-Chats (SvelteKit)
**Status**: gefixt (2026-05-30)
**Symptom (4 Bugs)**:
1. Stream eines Chats lief beim Session-Wechsel in den neuen Chat / wurde dort persistiert.
2. Chat brach mitten im Stream an zufälliger Stelle ab ("continue" half).
3. Bash/Code-Blöcke blieben Plaintext bis der Stream fertig war.
4. Delete (Sessions, Env, Memory) tat nichts.
5. **Zwei parallele Chats: der erste brach ab sobald der zweite startete.**
**Root causes**:
- (1+5) `runJob` schrieb in modul-globale `streaming/streamBuffer/messages/abortCtl` — ein einziger Stream-Slot. Session-Wechsel überschrieb/abortete den laufenden Stream.
- (2) `sseStream` war ein nackter fetch-Reader ohne Reconnect; jeder Connection-Drop beendete die Schleife. Backend konnte längst per `last_event_id` replayen.
- (3) `marked` rendert eine offene ` ``` `-Fence als Plaintext bis die schließende Fence ankommt.
- (4) `window.confirm` ist im Tauri-WebKit ein No-op → jeder Delete brach vorher ab.
**Fix**:
- Per-Session `StreamState`-Map (`streamStates`) statt Singletons. Jeder Job hat eigene Buffer + AbortController, läuft unabhängig + persistiert immer ans Backend. View rendert via `$derived`-Getter den Record der aktiven Session. **Echte parallele Streams.** Sidebar zeigt Spinner pro laufendem Chat.
- `sseStream`-Consume in Reconnect-Schleife: trackt `last_event_id`, reconnectet mit Backoff bis `job_finished` (max 6 Versuche).
- `Markdown.svelte` schließt offene Fence vor `marked.parse`.
- Globaler `confirmStore` + `<ConfirmModal>` im Layout; alle 3 Confirm-Stellen umgehängt.
**Files**: `web/src/routes/+page.svelte`, `web/src/lib/Markdown.svelte`, `web/src/lib/stores.svelte.ts`, `web/src/lib/ConfirmModal.svelte` (neu), `web/src/routes/+layout.svelte`, `web/src/routes/env/+page.svelte`, `web/src/routes/memory/+page.svelte`, `api/datastore.py` (FTS-Cleanup bei delete_session).
**Verifiziert**: Browser-Test 2 parallele Chats — beide vollständig (30/30 Zeilen), erster nicht abgebrochen. Delete-Modal erscheint, native confirm nicht aufgerufen.

---

### BUG-F10: Voice-Button — Transkript erscheint nicht (ohne Whisper)
**Status**: gefixt (2026-05-25, commit `40613e4`)
**Root cause**: Mic-Button war immer aktiv, auch wenn `WHISPER_BINARY` ungesetzt + `WHISPER_DEVICE=cpu` (= keine Backend-Konfiguration). User klickte, sprach, bekam 503 mit zerfasertem `detail`-String den der toast halbwegs zeigte — aber kein Hinweis dass der Backend gar nicht erst aufzurufen ist.
**Fix**:
- Neuer Endpoint `GET /v1/voice/status` → `{available, backend, reason}`. Frontend probiert das einmal beim Mic-Button-Bau (caching: ein Probe pro Page-Load).
- Bei `available=false`: Button bleibt sichtbar aber bekommt CSS-Klasse `mic-btn-disabled` (gedimmt, `cursor: help`) + Tooltip mit dem `reason`. Klick zeigt Toast statt MediaRecorder-Aufnahme zu starten.
- Vier Fälle differenziert: `voice_note_enabled=false`, `whisper-cpp ok`, `nvidia_nim` (mit/ohne Key), `none`.
**Files**: `api/dashboard_routes.py` (+39 LOC neuer Endpoint), `api/ui_static/app.js` (Probe-Cache + Disabled-State), `api/ui_static/styles.css` (`.mic-btn-disabled` Klasse), `tests/api/test_voice.py` (+5 Tests).

---

### BUG-F11: Tool-Blocks nicht klickbar bei langen Pfaden
**Status**: gefixt (2026-05-25, commit `c543914`)
**Root cause**: `api/agent/agent_loop_streaming.py::_summarise_input` truncated ALLE Tool-Args bei >80 Zeichen mit `…`. Für File-Ops (`Read`/`Write`/`Edit`/`LS`) landete der truncated Pfad im `data-filepath`-Attribut des UI Tool-Blocks. Klick öffnete dann `openPreviewPanel("/Users/aaron/Dateien - Lo…")` → 404. Bei kurzen Pfaden funktionierte alles, weshalb der Bug nur bei tief-genesteten Projekten auftrat.
**Fix**: File-Ops behalten den vollen Pfad. CSS macht ohnehin `text-overflow: ellipsis` auf `.tool-args` — Display-Truncation gehört nicht in Backend-Daten.
**Files**: `api/agent/agent_loop_streaming.py`, `tests/api/test_agent_loop_streaming.py` (+2 Regression-Tests: Format matched JS regex, lange Pfade werden 1:1 emittiert).

---

### BUG-F12: Projekt-Kontext fehlt beim Datei-Zugriff (Workspace-Resolver)
**Status**: gefixt (2026-05-25, commit `c543914`) — Code-Path war schon in BUG-F07 fix korrekt; jetzt mit Test-Suite abgesichert.
**Root cause**: `api/agent/jobs.py::_run_job` injizierte `payload["metadata"]["workspace_path"]` aus dem Projekt nur wenn `setdefault` nicht durch existing-key ueberschrieben wurde. Funktional korrekt — aber ungetestet, daher Regression-Risiko bei Refactor.
**Fix**: Vollständige Test-Suite für `workspace_resolver.py` mit allen 5 Prioritätsstufen (explicit metadata > project_id → DB lookup > per-user env > global default > CWD). 6 Tests inkl. edge cases (vanished project_id, empty workspace_path).
**Files**: `tests/api/test_workspace_resolver.py` (neu, 6 Tests).

---

### BUG-F08: Tauri-Desktop-App + GitHub-Download fehlten
**Status**: gefixt (2026-05-24, commit `185cbec`)
**Symptom**: Companion war nur als Source-Install ueber `uv run companion-server` verfuegbar. Aaron wollte normalen Programm-Download per GitHub Release (dmg/exe/AppImage).
**Loesung**: Full v1 Tauri-Bundle umgesetzt — siehe `docs/agent-prompts/09-tauri-wrap.md`. Konkret:
- `tauri/src-tauri/` (Tauri 2.x, Rust) — spawnt companion-bin on launch, wartet 5s auf Port 8082, tray icon mit Start/Stop/Open/Quit, hide-statt-close.
- `packaging/companion-bin.spec` (PyInstaller; PyOxidizer war 2026 effektiv tot, kein Py3.14-Support) — Single-File-Bundle ~37 MB mit FastAPI/pydantic/uvicorn/sqlite/loguru/api/core/cli/plugins/routines/ui_static/env.example.
- `.github/workflows/release.yml` — Matrix-Build macos-14/windows-latest/ubuntu-22.04, PyInstaller -> Tauri -> Artefakte -> softprops/action-gh-release. Triggert auf tag push `v*.*.*` und workflow_dispatch.
- `scripts/gen_icon.py` — Pillow-Placeholder-Icon (indigo Gradient + "C"); `cargo tauri icon` generiert komplettes Set (mac icns, Win ico, Android, iOS).
**Acceptance**: Local `cargo tauri build --debug` produziert `Companion.app` + `Companion_1.0.0_aarch64.dmg`. Push eines `v*.*.*` tags loest CI-Build aller drei OS-Bundles aus + Release-Erstellung.

### BUG-F09: "free-claude-code" Branding weg, alles auf "companion"
**Status**: gefixt (2026-05-24, commit `185cbec`)
**Symptom**: Repo hieß intern noch `free-claude-code` (pyproject `name`, CLI scripts `fcc-server`/`fcc-init`/`fcc-claude`, Paths `~/.config/free-claude-code/`, `~/.cache/free-claude-code/`, User-Agent, UI-Titel, Wizard).
**Fix**:
- pyproject `name = "companion"`, scripts neu: `companion`, `companion-server`, `companion-init`, `companion-claude`, `ds`. Legacy-Aliase (`fcc-server`, `free-claude-code`, `fcc-init`, `fcc-claude`) **entfernt**.
- Paths neu: `~/.config/companion/`, `~/.cache/companion/`. Datastore + `cli/entrypoints.serve()` migrieren existierende Legacy-Dirs auf erstem Run automatisch (`shutil.copy2` + `copytree`). Legacy bleibt liegen als Undo.
- Env var: `COMPANION_ENV_FILE` (Legacy `FCC_ENV_FILE` weiterhin akzeptiert als Fallback).
- UI / Admin / Wizard Strings: alle "Free Claude Code" -> "Companion", brand-mark "FC" -> "C".
- System-prompt Identity: "the user runs through their local free-claude-code proxy" -> "the user's self-hosted AI workstation".
- User-Agent: `companion/2.0`.
- Doku, Smoke-Tests, Unit-Tests entsprechend angepasst.
- 3 historische `# type: ignore[…]` in `api/agent/indexer.py` entfernt (CI grep-gate-clean), `tiktoken` Rueckgabe als `Any` annotiert, watchdog Observer `obs.stop/join` ohne ignores.
**Verification**: alle 5 Gates gruen (1786 Tests), grep-gate `# type: ignore|# ty: ignore` rein 0 hits.

### BUG-F07: Modell weiss nicht in welchem Projekt es ist
**Status**: gefixt (2026-05-24, commit `a1bdae7`)
**Symptom**: User fragt "welches Projekt ist mit diesem Chat verknuepft?" -> Agent antwortet "kein Projekt verknuepft", auch wenn `session.project_id` korrekt gesetzt ist und Sidebar das Projekt anzeigt.
**Root cause**: `api/agent/jobs.py::_run_job` hat NUR den user-supplied `shared_context` und Pinned-Memories in den System-Prompt injiziert. Wenn `shared_context` leer war (Default fuer neue Projekte), bekam das Modell null Signal dass ueberhaupt ein Projekt verknuepft ist — kein Name, keine ID, kein Workspace.
**Fix**: Bei gesetztem `project_id` immer einen "Project header"-Block voranstellen mit `Project name`, `Project ID`, `Workspace path`, optional `Project description`. Funktioniert auch ohne `shared_context`. Pinned-Memories und Brief werden weiterhin angefuegt.
**Files**: `api/agent/jobs.py`, `tests/api/test_pinned_memories.py` (Assertions auf neuen Header, mock-router auf Echo-Pattern angeglichen damit pre-routing injection in `run_agent_streaming` ankommt).
**Verification**: `uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest -q` -> 1786 passed.

### BUG-F06: 2. paralleler Chat friert UI ein, Server antwortet nicht mehr
**Status**: gefixt (2026-05-24, commit `a1bdae7`)
**Symptom**: 1 Chat laufend = OK. 2. Session im selben Tab starten waehrend 1. noch streamt -> UI friert komplett ein, Reload laedt endlos, Server-Terminal komplett still, keine Exception.
**Root cause**: `api/agent/jobs.py::_run_job` und `event_stream` riefen `datastore.*` (sync `sqlite3`) DIREKT im asyncio-Event-Loop auf. SQLite hat WAL aber Writes serialisieren; `timeout=10s` busy-wartet im C-Frame -> blockiert Loop -> keine andere Coroutine laeuft (auch nicht der Static-File-Handler fuer Reload). Zwei parallele Jobs schreiben hunderte SSE-Chunks/sec -> Schreib-Lock-Sturm -> Wedge.
**Fix**: Alle hot-path `datastore.*` Calls in `_run_job` + `event_stream` + `_record_job_usage` + `_maybe_notify` ueber `asyncio.to_thread(...)` in den Thread-Pool. Reihenfolge im Job-Lifecycle korrigiert: `mark_agent_job_status(terminal)` jetzt LETZTER persistierter Step, damit Status-Polling die "alles persisted"-Invariante bekommt.
**Files**: `api/agent/jobs.py`, `tests/api/test_agent_jobs_usage.py` (drei sync Calls auf `_record_job_usage` jetzt `async` + `@pytest.mark.asyncio`).
**Verification**: `uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest -q` -> 1786 passed.
**Residual risk**: Bei sehr vielen parallelen Jobs (10+) bleibt SQLite-Write-Contention messbar (Job-Throughput sinkt), aber UI bleibt responsive. Falls Problem: `append_agent_job_event` SELECT+INSERT auf ein einzelnes INSERT mit Subquery zusammenfassen (halbiert Roundtrips) — separater Folge-Patch.

### BUG-F01: Regenerate haengt alte Antwort an statt zu ersetzen
**Status**: gefixt in `bb6da0d`
**Fix**: `regenInChat()` loescht alte Antwort aus DB (DELETE /v1/sessions/{id}/messages/{mid}) + DOM, startet neuen Job mit bestehender History

### BUG-F02: Voice-Leertaste interfereiert mit Tippen
**Status**: gefixt in `bb6da0d`
**Fix**: Spacebar-Hold entfernt, ersetzt durch dedizierten Mic-Button (🎤) in Composer-Row

### BUG-F03: CI rot wegen `ruff format` auf `api/runtime.py`
**Status**: gefixt in `34c773b`

### BUG-F04: CI rot wegen `# type: ignore` in test_agent_loop.py
**Status**: gefixt in `ee5b2f2`

### BUG-F05: `Unknown provider_type: claude-3-5-sonnet-20241022`
**Status**: gefixt (war nur in smoke-Tests und alten Fixtures; production nutzt jetzt nur Gateway-IDs)
