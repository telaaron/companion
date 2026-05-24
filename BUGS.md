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

### BUG-001: File Preview Panel — Tool-Blocks nicht klickbar
**Status**: offen
**Symptom**: Agent liest README.md, aber kein klickbarer Tool-Block erscheint. Rechter Preview-Panel oeffnet nicht.
**Reproduzieren**: Chat starten → "Lies meine README.md" → Tool-Block kommt als Text, kein klickbarer Link
**Erwartet**: Tool-Block zeigt "● Read(pfad)" als klickbares Element → Klick oeffnet File-Preview-Panel rechts
**Dateien**:
- `api/ui_static/app.js` — Funktion `_trackMsg()` um Zeile 1124, Event-Delegation auf `messages` um Zeile 1087
- CSS-Klasse `.tool-block-clickable` in `api/ui_static/styles.css`
**Notizen**: Preview-Panel-Code ist in `openPreviewPanel()`. Der Endpoint `/v1/preview/file?path=...` existiert in `api/dashboard_routes.py`.

---

### BUG-002: Projekt-Kontext fehlt beim Datei-Zugriff
**Status**: offen
**Symptom**: Agent wird nach "meine README.md" gefragt, sucht global ueberall statt im Projekt-Workspace
**Reproduzieren**: Projekt "companion" anlegen, Workspace auf `/Users/.../companion` setzen → Chat im Projekt-Kontext → "Lies README.md" → Agent sucht in Home-Dir
**Erwartet**: Agent liest zuerst aus dem konfigurierten Workspace-Pfad des aktiven Projekts
**Dateien**:
- `api/agent/jobs.py` — `_run_job()`: workspace resolution, `project_id` ist verfuegbar
- `api/agent/workspace_resolver.py` — `AGENT_DEFAULT_WORKSPACE_<user>` Env-Override
- `api/dashboard_routes.py` — `GET /v1/sessions/{id}` gibt `project_id` zurueck
**Notizen**: Das Projekt hat ein `workspace_path` Feld in der DB (`projects.workspace_path`). Dieser muss als default Workspace des Jobs gesetzt werden wenn `project_id` gesetzt ist.

---

### BUG-003: Voice-Button — Transkript erscheint nicht (ohne Whisper)
**Status**: offen
**Symptom**: Mic-Button erscheint, Aufnahme startet (pulsiert rot). Aber nach Stop: kein Text im Input-Feld
**Reproduzieren**: Mic-Button klicken, sprechen, nochmal klicken → Input bleibt leer
**Erwartet**: Transkript erscheint im Textarea
**Dateien**:
- `api/dashboard_routes.py` — `POST /v1/transcribe` Endpoint
- `config/settings.py` — `whisper_binary`, `whisper_model`
**Notizen**: Wenn `WHISPER_BINARY` nicht gesetzt ist, gibt der Endpoint vermutlich Fehler zurueck oder leeren Text. Endpoint koennte auch einen sinnvolleren Fehler zurueckgeben oder fallback anbieten. Pruefen: was gibt `/v1/transcribe` ohne Whisper zurueck?

---

## Gefixte Bugs

### BUG-F07: Modell weiss nicht in welchem Projekt es ist
**Status**: gefixt (uncommitted, 2026-05-24)
**Symptom**: User fragt "welches Projekt ist mit diesem Chat verknuepft?" -> Agent antwortet "kein Projekt verknuepft", auch wenn `session.project_id` korrekt gesetzt ist und Sidebar das Projekt anzeigt.
**Root cause**: `api/agent/jobs.py::_run_job` hat NUR den user-supplied `shared_context` und Pinned-Memories in den System-Prompt injiziert. Wenn `shared_context` leer war (Default fuer neue Projekte), bekam das Modell null Signal dass ueberhaupt ein Projekt verknuepft ist — kein Name, keine ID, kein Workspace.
**Fix**: Bei gesetztem `project_id` immer einen "Project header"-Block voranstellen mit `Project name`, `Project ID`, `Workspace path`, optional `Project description`. Funktioniert auch ohne `shared_context`. Pinned-Memories und Brief werden weiterhin angefuegt.
**Files**: `api/agent/jobs.py`, `tests/api/test_pinned_memories.py` (Assertions auf neuen Header, mock-router auf Echo-Pattern angeglichen damit pre-routing injection in `run_agent_streaming` ankommt).
**Verification**: `uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest -q` -> 1786 passed.
**Folgeaktion fuer Aaron**: Companion-Projekt im UI hat laut Chat `workspace_path = ~/free-claude-code` (stale). Sollte auf `~/Dateien - Local/companion` umgesetzt werden, sonst loesen File-Tools relative Pfade falsch auf (siehe auch BUG-002).

### BUG-F06: 2. paralleler Chat friert UI ein, Server antwortet nicht mehr
**Status**: gefixt (uncommitted, 2026-05-24)
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
