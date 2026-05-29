# Contributing to Companion

Thanks for thinking about contributing. Whether it's a bug report, a doc
fix, or a new feature — every PR is welcome.

This document tells you what we expect, how to set up a dev environment,
and how a change gets from your fork into a release.

---

## Code of conduct

Be respectful. Assume good faith. If something feels off, flag it in a
Discussion or contact `admin@must-seen.com` privately.

---

## Quick links

- 🐞 **Bug** → open an [Issue](https://github.com/telaaron/companion/issues/new/choose) with the `bug.yml` template
- 💡 **Feature idea** → open a [Discussion](https://github.com/telaaron/companion/discussions/new?category=ideas) first, then an issue if it's actionable
- ❓ **Question** → [Discussions Q&A](https://github.com/telaaron/companion/discussions/new?category=q-a)
- 🔒 **Security** → see [SECURITY.md](SECURITY.md), never use a public issue

---

## Dev environment

You need:

- macOS 12+, Linux x86-64, or Windows 10/11
- Python **3.14** (managed by `uv`)
- Node **22** (for the SvelteKit UI)
- Rust **stable** + `tauri-cli` (only if you touch the desktop shell)

### One-time setup

```bash
# Clone
git clone https://github.com/telaaron/companion.git
cd companion

# Install Python toolchain (uv) and project deps
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --dev
uv python install 3.14

# Install web deps
cd web && npm ci && cd ..

# Optional: Tauri CLI for desktop builds
cargo install tauri-cli --version "^2" --locked
```

### Day-to-day

Terminal 1 — backend with auto-reload:

```bash
uv run companion-server
```

Terminal 2 — frontend HMR:

```bash
cd web && npm run dev
```

Open <http://localhost:5173>. Vite proxies `/v1/*` to the FastAPI server on
`:8082`. Changes to `.svelte` files hot-reload in < 100 ms. Python changes
need a server restart.

You do **not** need to rebuild the Tauri desktop bundle during normal
development — only when packaging a release.

---

## Checks (run before every commit)

The CI gate enforces all five. Run them locally first:

```bash
uv run ruff format
uv run ruff format --check
uv run ruff check
uv run ty check
uv run pytest -v --tb=short
```

There's also a grep gate forbidding `# type: ignore` and `# ty: ignore`
in the tree — fix the underlying type instead.

For UI-only changes, additionally run:

```bash
cd web && npm run check
```

---

## Coding conventions

- **Python 3.14** with `from __future__ import annotations` where helpful.
- **Type-checked**. We run `ty`; no `# type: ignore` escape hatches.
- **Tests**: every fix needs a regression test; every feature needs at
  least one end-to-end test.
- **No new top-level dependencies** without a note in the PR description
  explaining why.
- **TypeScript** in the web tree. Avoid `any` outside `Record<string, unknown>`
  bridging.
- **Svelte 5 runes** (`$state`, `$derived`, `$effect`) — don't use
  legacy `let` reactivity in new components.

### Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <imperative summary>

<longer body explaining why the change is needed>

Co-Authored-By: ...
```

Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `style`,
`perf`, `build`, `ci`.

Examples:

- `fix(web/chat): tool-block cards collapse on reload`
- `feat(routines): live cron preview in create drawer`
- `docs(security): clarify 30-day disclosure window`

---

## Pull request flow

1. Fork the repo or push to a topic branch on your clone.
2. Open the PR against `main`.
3. Fill in the PR template — what changed, why, screenshots if UI.
4. The CI runs five gates plus a smoke build. Keep pushing until green.
5. Ask for review when ready (`@telaaron`).
6. Once approved + green, the PR is squash-merged. Your commits get
   collapsed into one conventional-commit on `main`.

Smaller PRs land faster. Aim for < 400 lines of diff when you can.

---

## Adding a feature

1. **Open a Discussion first** if the design isn't obvious. We'd rather
   talk about an API before you've spent an evening implementing it.
2. **Write the test first** when it's mechanical. End-to-end test in
   `tests/api/` for backend changes, Vitest if we add it for the UI.
3. **Update the docs** — `README.md` if user-visible, `docs/` for
   architecture, the relevant `+page.svelte` for an inline help string.
4. **Add a `CHANGELOG.md` entry** under `## [Unreleased]`.

---

## Reviewing other people's PRs

You don't need write access to leave a review. Pick a PR, read it, leave
comments. We pay attention to:

- Correctness — does it do what the PR says?
- Tests — does it have them, do they catch the bug?
- Breaking changes — flagged in CHANGELOG?
- Migration story — schema bump? bumped `_SCHEMA_VERSION`?

---

## Release process

Releases go through `docs/RELEASE_PROCESS.md`. The TL;DR:

- `vX.Y.Z` tag on `main` → stable release for everyone
- `vX.Y.Z-beta.N` on `beta` branch → opt-in tester channel
- Nightly builds run automatically at 03:00 UTC

You don't normally cut a release — the maintainer does. If you need a fix
to ship, ping in the PR.

---

## Where things live

| Folder | What |
|---|---|
| `api/` | FastAPI app, routes, dashboard endpoints |
| `core/` | Provider router, workspace, tool registry |
| `providers/` | DeepSeek, OpenRouter, NIM, Anthropic adapters |
| `web/` | SvelteKit dashboard (the thing at `/ui`) |
| `tauri/` | Desktop shell + auto-updater |
| `packaging/` | PyInstaller spec for the frozen `companion-bin` |
| `plugins/` | YAML-defined plugin specs (Obsidian, journal, …) |
| `routines/` | Starter cron-job templates |
| `tests/` | pytest suite (~1809) |
| `docs/` | Architecture + ops docs |

Open the file, read the top-of-file docstring, and the surrounding code
will usually tell you what's idiomatic.

---

## License

By contributing you agree your work ships under the project's MIT license.
See [LICENSE](LICENSE).
