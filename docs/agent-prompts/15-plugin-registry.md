# 4.2 — Plugin registry

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

`companion plugins install linear` downloads + verifies a signed plugin
YAML from a community-curated index hosted at
`github.com/telaaron/companion-plugins`.

## Files

- `cli/entrypoints.py` — new `plugins` subcommand with `list`, `search`,
  `install`, `remove`, `update`.
- `api/agent/plugin_registry.py` (new) — fetches the catalog JSON,
  verifies signatures, installs to `plugins/`.
- The remote repo `telaaron/companion-plugins` (out of scope for this
  PR — coordinate separately) hosts:
  - `index.json` listing every plugin with `name, kind, version, url, sha256, signature`.
  - One signed `.yaml` per plugin under `plugins/`.
- `tests/cli/test_plugins_cli.py` (new) — uses a fixture catalog served
  by a tmp HTTP server.

## Implementation plan

1. Define the catalog schema. Use Ed25519 detached signatures
   (`cryptography` library) signed by a project-controlled key whose
   pubkey is baked into the client.
2. `companion plugins install linear` flow:
   - GET the catalog.
   - Find `linear` entry, GET the YAML + signature.
   - Verify `sha256` and signature. Refuse on any mismatch.
   - Write to `plugins/linear.yaml`.
   - Print: "Restart the server to load the new plugin."
3. `companion plugins list` shows local + remote with version drift
   indicators.
4. Tests: tmp HTTP server serves a fixture catalog + signed YAML. Install
   → file appears in plugins dir. Tamper with the YAML → install fails.

## Acceptance

- Fresh repo: `companion plugins install linear` works without manual
  YAML editing. Restart → Linear MCP server registered.
- Tampering with the published YAML (or signature) makes install fail
  with a clear error.

## Risks

- Signature scheme: store the pubkey in `cli/keys.py` constant. Rotate
  via a new release if compromised.
- HTTP failures must not corrupt `plugins/` — download to tmpfile first,
  rename on success.

## Verify

```bash
uv run pytest tests/cli/test_plugins_cli.py -v
uv run pytest -v --tb=short
```
