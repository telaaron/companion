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
