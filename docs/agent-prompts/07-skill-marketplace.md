# 2.2 — Skill marketplace

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Browse a curated catalog of Anthropic-style skills (PDF, Excel,
PowerPoint, Docx, image-gen, …) inside `/ui` → Skills. Click "Install"
to make them available to the agent.

## Files

- `skills/` (new dir at repo root) — each skill = folder with `SKILL.md`
  (description + when-to-use) + scripts + assets. Mirror the
  `anthropic-skills` plugin layout.
- `api/agent/extras/skills.py` — register `SkillList()` and
  `SkillRun(name, args)` tools. `SkillRun` looks up `skills/{name}`,
  resolves the entry script, runs it with `args` in a subprocess
  (`asyncio.create_subprocess_exec`), captures stdout/stderr, returns a
  `ToolResult`. 60 s timeout. Hard kill on timeout.
- `api/dashboard_routes.py`:
  - `GET /v1/skills/local` returns installed skills.
  - `GET /v1/skills/catalog` returns parsed `index.json` from
    `https://raw.githubusercontent.com/anthropic-skills/index/main/index.json`
    (configurable). Cache 24 h on disk.
  - `POST /v1/skills/install/{slug}` downloads + extracts a `.tar.gz`
    from the catalog into `skills/{slug}`. Validate filename + extracted
    paths don't escape.
- `api/ui_static/app.js` — new "Skills" page module: local list +
  remote catalog + install button.
- `config/settings.py` — `SKILLS_CATALOG_URL` setting.
- `tests/agent/test_skills.py` (new) — install a fixture skill, run it,
  verify output.

## Implementation plan

1. Skill loader: walk `skills/*/SKILL.md`, parse front-matter
   (`name`, `description`, `entry`, `args_schema`). Build a `Skill`
   dataclass per dir. Re-scan on `runtime.startup`.
2. `SkillRun(name, args)` tool: resolves the skill, runs `python entry.py
   <args-as-JSON>`, returns stdout. Stderr is included in the tool result
   on non-zero exit.
3. UI: card per local skill with version + uninstall button. Catalog
   shown below in a grid with install buttons. Confirm dialog on install:
   *"This will download and execute code from {url}. Continue?"*
4. Tests: fixture skill at `tests/fixtures/skills/echo/`. Install via the
   API, invoke `SkillRun("echo", {"text": "hi"})`, assert tool result.

## Acceptance

- Skills page → click "Install PDF" → confirmation → installed skill
  appears in local list → chat: *"extract page 5 of this PDF"* → agent
  calls the PDF skill → output appears in the reply.
- Uninstall removes the folder; SkillList reflects the change.

## Risks

- Skills are arbitrary code. Mark unsigned catalog entries as
  warn-on-install. Block any skill whose `entry` resolves outside its own
  folder.
- Subprocess cleanup on timeout — use `proc.kill()` + `await proc.wait()`.

## Verify

```bash
uv run pytest tests/agent/test_skills.py -v
uv run pytest -v --tb=short
```
