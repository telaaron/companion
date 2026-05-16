# 2.4 — Tauri desktop wrap

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Package the dashboard as a native macOS / Windows / Linux binary that
bundles the proxy. Users get a `Companion.app` icon, double-click it,
dashboard opens, no terminal seen.

## Files

- `tauri/` (new) — Tauri 2.x project at the repo root.
  - `tauri.conf.json` — app id `com.companion.app`, window
    `http://127.0.0.1:8082/ui/`, identifier, icons.
  - `src-tauri/src/main.rs` — on launch: spawn the bundled Python entry
    `companion-bin` (PyOxidizer or Briefcase-built single-file binary),
    wait for `127.0.0.1:8082` to accept connections (5 s budget), open
    the webview. Tray icon: Start / Stop / Open Dashboard / Quit.
  - `src-tauri/Cargo.toml`.
- `packaging/pyoxidizer.bzl` (or `pyproject.toml` for Briefcase) — config
  for the single-file Python binary that ships with the Tauri bundle.
- `.github/workflows/release.yml` — matrix of (`macos-14`, `windows-latest`,
  `ubuntu-22.04`). Steps: build python binary → build Tauri → upload
  artifact. Tag push triggers a release with the bundles attached.
- `docs/installing.md` — download links + walkthrough.

## Implementation plan

1. Choose the bundler. **Recommendation: PyOxidizer** for the embedded
   Python (single ~80 MB binary, no system Python needed). Briefcase
   works but the resulting bundle is larger.
2. Verify the `fcc-server` entrypoint runs from a frozen binary
   (filesystem paths, resource loading, etc. — likely needs
   `importlib.resources` instead of `__file__` reads).
3. Tauri side: tray icon + window. Window points at the localhost URL.
   Tauri's `BeforeBundle` hook copies the python binary into the bundle.
4. CI: build matrix per OS. macOS arm64 + x86_64, Windows x86_64, Linux
   x86_64. Codesigning for macOS: stub for now, document the cert
   requirement in `docs/releasing.md`.
5. Release: GitHub release on tag push. Artifacts named
   `Companion-{version}-{arch}.{dmg,exe,AppImage}`.

## Acceptance

- Download `Companion-1.0.0-arm64.dmg` on a fresh Mac → drag to
  Applications → launch → dashboard opens within 5 s → no terminal seen.
- Quit from the tray → process tree dies cleanly.
- Linux AppImage + Windows installer follow the same flow.

## Risks

- Bundling Python is the hard part. PyOxidizer is opinionated; allow a
  fallback to Briefcase if it doesn't fit.
- Codesigning for macOS Gatekeeper-clean install needs an Apple Developer
  account — out of scope for v1, document the unsigned warning.

## Verify

```bash
cd tauri && cargo tauri build --debug
# launch the built binary, confirm dashboard loads
```
