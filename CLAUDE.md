IMPORTANT: Ensure you've thoroughly reviewed the [AGENTS.md](AGENTS.md) file before beginning any work.

---

# Companion — AI-Workstation Kontext

> Lies das bevor du irgendetwas anfasst. Alles was du brauchst ist hier.
> Aktuelle Bugs: [BUGS.md](BUGS.md)

---

## Was ist Companion?

Selbst-gehostetes KI-Workstation-Backend + Dashboard.
- FastAPI-Proxy der Anthropic Messages API (spricht mit DeepSeek, OpenRouter, Gemini, NIM, ...)
- Vanilla JS SPA ohne Build-Step
- SQLite-DB mit FTS5-Volltext-Suche
- Hintergrund-Agent-Loop (Jobs) mit SSE-Streaming
- MCP-Passthrough (Obsidian, Firecrawl, Email, ...)
- Branch: `feat/agentic-os-phase-b` auf `telaaron/companion` + `telaaron/companion`

---

## Repo-Struktur (das Wichtigste)

```
api/
  app.py                 ASGI-App, mounted router + static UI
  routes.py              /v1/messages proxy (Anthropic-kompatibel)
  dashboard_routes.py    Alle internen /v1/* Endpoints fuer das UI
  dependencies.py        FastAPI DI: require_api_key, get_current_user_id
  datastore.py           SQLite schema + alle DB-Helfer-Funktionen
  runtime.py             Startup/Shutdown: MCP, Indexer, Scheduler
  auth.py                Bearer + Cloudflare Access JWT Verifikation
  pricing.py             Dollar/Mtok Preistabelle

  agent/
    agent_loop.py        Multi-Turn-Agent-Loop, Tool-Dispatch
    jobs.py              Hintergrund-Jobs + SSE-Pump (ALLES laeuft hier durch)
    mcp.py               JSON-RPC MCP Client + Auto-Discovery
    indexer.py           Filesystem-Scanner fuer RAG (watchdog)
    routines.py          Routine-Scheduler (Cron-Jobs)
    insights.py          Nutzungsanalyse + Suggestions
    journal.py           Tages-Journal (Obsidian)
    notifier.py          Slack/Discord Webhooks
    extras/
      skills.py          SkillRun/SkillList Tools
      search.py          Memory FTS Search Tool
      preferences.py     PreferenceSet/Delete/List Tools

  ui_static/
    app.js               GESAMTE Frontend-Logik (~4000 Zeilen vanilla JS)
    styles.css           CSS Custom Properties, kein Framework
    index.html           Shell

config/settings.py       pydantic-settings, alle Env-Vars
core/model_router.py     Splittet "provider/model" Gateway-IDs
cli/entrypoints.py       Console-Scripts: companion-server, companion plugins, discord-bot
plugins/                 YAML Plugin-Specs (obsidian, journal, mcp-server, ...)
routines/                Starter-Routine-YAML-Templates
tests/                   pytest Suite (~1786 Tests, parallel via xdist)
docs/agent-prompts/      22 selbstaendige Briefs fuer jeden Feature-Bereich
deploy/                  Docker + Tailscale Deploy-Recipes
scripts/remote-up.sh     Cloudflare Tunnel Quickstart
tauri/                   Desktop-App Scaffold (Rust/Tauri 2.x)
```

---

## Dev-Befehle

```bash
# Server starten (http://127.0.0.1:8082/ui/)
uv run companion-server

# Checks (IN DIESER REIHENFOLGE)
uv run ruff format             # Formatierung auto-fix
uv run ruff format --check     # Formatierung pruefen
uv run ruff check              # Linting
uv run ty check                # Typen
uv run pytest -v --tb=short    # Tests (1786 baseline)

# Alle auf einmal (muss alles gruen sein)
uv run ruff format && uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest -v --tb=short

# Deps installieren / aktualisieren
uv sync
```

---

## Harte Regeln (CI bricht wenn verletzt)

| Regel | Warum |
|---|---|
| Kein `# type: ignore` oder `# ty: ignore` | CI grep-Schritt faellt raus |
| `ruff format --check` muss gruen sein | CI-Schritt 2 |
| Keine blanken Claude-IDs (`claude-3-5-sonnet-20241022`) | model_router wirft ValueError |
| Tests >= 1786 | Kein Regress erlaubt |
| Kein `except A, B:` (Python 2) | Nutze `except (A, B):` |

---

## Wichtige Code-Muster

### DB-Schema-Migration
```python
# In api/datastore.py
_SCHEMA_VERSION = 4  # bump wenn neue Migration noetig

# In init_schema(), nach executescript:
ver = conn.execute("PRAGMA user_version").fetchone()[0]
if ver < 4:
    conn.execute("ALTER TABLE foo ADD COLUMN bar TEXT DEFAULT ''")
    conn.execute("CREATE TABLE IF NOT EXISTS new_table (...)")
    conn.execute("PRAGMA user_version = 4")
```
IMMER `CREATE TABLE IF NOT EXISTS` — idempotent, zweimal laufen = OK.

### FastAPI DI
```python
from api.dependencies import require_api_key, CurrentUserId

@dashboard_router.get("/v1/something")
async def my_endpoint(
    user_id: CurrentUserId,           # multi-user scoping
    _auth=Depends(require_api_key),   # auth gate
) -> dict[str, Any]: ...
```

### Neue Endpoints
Alle internen Endpoints in `api/dashboard_routes.py`, nicht in `api/routes.py`.

### Model-Routing
```python
from core.model_router import ModelRouter
router = ModelRouter(settings)
provider_id, provider_model = router.resolve("deepseek/deepseek-v4-flash")
# NIEMALS direkte Claude-ID: "claude-3-5-sonnet-20241022"
```

### Frontend (app.js)
Kein React. Vanilla JS mit dem `el()` helper:
```js
const div = el("div", { class: "my-class", onclick: handler }, "text");
// entspricht: createElement + setAttribute + textContent
```

Neue UI-Seite hinzufuegen:
1. `renderPage("meine-seite")` im Switch registrieren
2. `renderMeineSeite(host)` Funktion schreiben
3. Nav-Item in `index.html` oder app.js eintragen

---

## Wie Chat funktioniert (Jobs-Flow)

```
User tippt + sendet
  -> POST /v1/sessions/{id}/jobs          (jobs.py: _run_job startet)
  -> agent_loop.py: run_agent_streaming()
  -> SSE-Events in DB: agent_job_events
  -> UI: streamJobIntoChat()
  -> GET /v1/jobs/{id}/events (SSE-Stream)
  -> Chunks erscheinen in Chat-Bubble
  -> Job fertig: appendMessage() persistiert finale Antwort
```

---

## app.js Landkarte (Zeilen-Referenz)

| Funktion | Was sie tut |
|----------|-------------|
| `el()` | DOM-Element-Builder |
| `api()` | fetch-Wrapper mit Auth |
| `renderPage()` | Page-Router |
| `renderChatMain()` | Chat-UI aufbauen |
| `renderChatMessage(msg, ctx)` | Einzelne Nachricht rendern, haengt ↺ Button an |
| `sendInChat()` | Neuen Job starten + in Chat streamen |
| `streamJobIntoChat()` | SSE pump |
| `attachRegenerateButton()` | ↺ Button mit Model-Picker |
| `regenInChat()` | Alte Antwort DELETE + neuer Job |
| `_buildVoiceButton(ta)` | Mic-Button fuer Voice-Input |
| `openPreviewPanel()` | File-Preview Panel rechts |

---

## Tests schreiben

Muster aus `tests/api/test_e2e_agent_mode.py`:
```python
from api.agent.agent_loop import run_agent_to_completion
from core.tools.workspace import Workspace

def _make_request():
    return MessagesRequest(
        model="x",                   # kein echter Provider noetig
        max_tokens=128,
        messages=[Message(role="user", content="go")],
    )

class _FakeProvider(BaseProvider):
    def __init__(self, turns):
        self._turns = iter(turns)
    async def stream_response(self, ...):
        yield next(self._turns)

async def test_something(tmp_path):
    provider = _FakeProvider([...])
    run = await run_agent_to_completion(_make_request(), provider, Workspace.create(tmp_path))
    assert run.turns == 1
```

Endpoint-Tests: `TestClient(app)` aus `fastapi.testclient`.
DB-Tests: `monkeypatch` auf `datastore._DB_PATH = tmp_path / "test.db"`.

---

## Bug fixen - Workflow

1. **Lies [BUGS.md](BUGS.md)** — Aaron listet dort Bugs mit Symptom + Reproduktion
2. **Grep nach betroffenem Code** (Datei-Tabelle oben)
3. **Minimale Aenderung** — nur Symptom fixen, kein Refactor drumherum
4. **Verifizieren**:
   ```bash
   uv run ruff format && uv run ruff check && uv run ty check
   uv run pytest -v --tb=short  # muss >= 1786 bestehen
   ```
5. **Commit** auf `feat/agentic-os-phase-b`:
   ```bash
   git add <files>
   git commit -m "fix(<scope>): <was und warum>"
   git push fork feat/agentic-os-phase-b
   git push companion feat/agentic-os-phase-b:main
   ```

---

## Haeufige Fehler + Fixes

| Symptom | Ursache | Fix |
|---|---|---|
| `Unknown provider_type: 'claude-3...'` | Blanke Claude-ID | Nutze `open_router/anthropic/claude-3.5-sonnet` |
| `ruff format --check` rot | Datei nicht formatiert | `uv run ruff format <datei>` |
| `SIM105` lint-Fehler | `try/except/pass` | `with contextlib.suppress(XError):` |
| Tests sinken um 15-20 | Agent auf altem Branch | `git fetch fork feat/agentic-os-phase-b && git checkout -B local-base FETCH_HEAD` |
| `ty: ignore` im Diff | Agent hat es eingefuegt | Entfernen, Typ-Problem direkt loesen |
| Voice-Button erscheint nicht | `getUserMedia` nicht verfuegbar | Nur auf localhost/HTTPS; Check: `navigator.mediaDevices` |
| Regenerate haengt | `message_id` fehlt in `data-msg-id` | renderChatMessage uebergibt `msg.id` ans article-Attribut |
| DB-Migration laeuft doppelt | `_SCHEMA_VERSION` nicht gebumpt | Version erhoehen + gaten |
| SSE stoppt nach Tab-Wechsel | visibilitychange-Listener | streamJobIntoChat hat reconnect-Logik, pruefen ob Listener aktiv |

---

## Bekannte Architektur-Entscheidungen

| Entscheidung | Warum |
|---|---|
| Kein React/Vue | Kein Build-Step, sofort editierbar ohne npm |
| Jobs statt direktes Streaming | Tab-Close killt nicht den Job; Background-Prozess |
| SQLite statt Postgres | Lokal, zero-config, FTS5 built-in |
| pydantic-settings validation_alias dict-Form | Vermeidet pydantic v2 Breaking-Change |
| Multi-user default="default" | Bestehende Single-User-DBs bleiben kompatibel |
| `api/dashboard_routes.py` fuer interne Endpoints | Trennung: `/v1/messages` (Proxy) vs `/v1/*` (Dashboard) |

---

*Stand: 2026-05-19, Branch `feat/agentic-os-phase-b`, SHA `bb6da0d`, 1786 Tests*
