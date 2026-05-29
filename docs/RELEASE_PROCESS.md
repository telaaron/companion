# Release Process

This is the single source of truth for shipping Companion. Read it once,
then bookmark the Quick-Reference at the bottom.

---

## Versioning

Strict [SemVer](https://semver.org/spec/v2.0.0.html): `MAJOR.MINOR.PATCH`.

| Change | Bump | Example | Cadence |
|---|---|---|---|
| Bug, typo, copy fix | **PATCH** | 1.1.3 → 1.1.4 | Batch ≥ 3 fixes OR ≥ 24h idle |
| New page / visible feature | **MINOR** | 1.1.x → 1.2.0 | Weekly with changelog highlight |
| Breaking change (API / storage / non-revertible migration) | **MAJOR** | 1.x → 2.0.0 | Quarterly, RFC required |
| Pre-release | suffix | `1.2.0-beta.1`, `1.2.0-rc.1` | As needed |

**Where the version lives** (keep all three in sync):

- `tauri/src-tauri/Cargo.toml`
- `tauri/src-tauri/tauri.conf.json`
- `web/package.json`

Use `scripts/bump-version.sh X.Y.Z` once that script is committed; until
then bump all three by hand.

---

## Channels

| Channel | Branch | Tag pattern | Manifest URL |
|---|---|---|---|
| **Stable** | `main` | `vX.Y.Z` | `latest.json` |
| **Beta** | `beta` | `vX.Y.Z-beta.N` | `latest-beta.json` |
| **Nightly** | `main` (built on cron) | `nightly-YYYYMMDD` | `latest-nightly.json` |

The auto-updater inside Companion picks its channel from the
`UPDATER_CHANNEL` env var (defaulting to `stable`). Users opt into Beta or
Nightly via Settings → Updates → Channel.

---

## Build flow

### Phase A — While developing

Zero builds needed:

```bash
# Terminal 1: backend
uv run companion-server                    # :8082

# Terminal 2: frontend HMR
cd web && npm run dev                      # :5173 (proxies /v1/* to :8082)

# Browser: http://localhost:5173
```

Backend change → restart server (~25 s incl. MCP bootstrap). Frontend
change → Vite HMR < 100 ms. **Never rebuild the Tauri bundle during
day-to-day coding.**

### Phase B — PR merged to main

Push to `main` triggers `tests.yml`:

1. `ruff format --check`
2. `ruff check`
3. `ty check`
4. grep guard (no `# type: ignore` / `# ty: ignore`)
5. `pytest`

All green = ready to tag.

### Phase C — Cut a release

Stable:

```bash
# Bump versions in 3 files
scripts/bump-version.sh 1.2.0

# Commit + tag
git add -A
git commit -m "chore: release v1.2.0"
git push origin main
git tag -a v1.2.0 -m "v1.2.0 — <one-line highlight>"
git push origin v1.2.0
```

Tag-push triggers `release.yml`:

1. PyInstaller bundle on macOS / Windows / Linux runners → frozen `companion-bin`.
2. SvelteKit static build for each target.
3. Tauri bundle → DMG / exe / AppImage + signed updater payloads
   (`.tar.gz.sig`, `.zip.sig`, `.AppImage.sig`).
4. `build_latest_json.py` walks the artefacts and writes `latest.json`
   with per-platform URLs + signatures.
5. GitHub Release published with all assets.

Beta:

```bash
git checkout beta
git merge --no-ff main      # only when beta-specific changes are involved
git tag v1.2.0-beta.1
git push origin beta v1.2.0-beta.1
```

Nightly is automatic — see `.github/workflows/nightly.yml`. No manual tag.

### Phase D — Verify

```bash
gh run watch                # CI progress
gh release view v1.2.0      # 6 assets expected: 3 installers + latest.json + 2 sigs
```

Manual smoke tests:

1. Download DMG → install → launch → UI loads → chat works.
2. From an old build, wait for auto-updater to fetch the new release →
   relaunch → verify new version string in **Settings → Server**.

---

## CI secrets

| Secret | Used in | How to set |
|---|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | `release.yml` Tauri build | `cat tauri/companion-updater.key \| gh secret set TAURI_SIGNING_PRIVATE_KEY` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Same (empty for dev key) | `gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --body ""` |
| `GITHUB_TOKEN` | Release creation | Provided automatically |

**Never commit**:

- `tauri/companion-updater.key` (private — in `.gitignore`)
- The pub key is fine; it's embedded in `tauri.conf.json`.

---

## Hot-fix flow

User reports a serious bug in stable `v1.2.3`:

```bash
git checkout -b hotfix/v1.2.4 v1.2.3
# fix it
git commit -m "fix(scope): ..."
git checkout main
git merge --no-ff hotfix/v1.2.4
git tag v1.2.4
git push origin main v1.2.4
```

Auto-updater rolls out within minutes. Delete the `hotfix/*` branch.

---

## Rollback

If a release went out broken:

1. `gh release delete v1.2.4`
2. `git push origin :refs/tags/v1.2.4`
3. `gh release edit v1.2.3 --latest`
4. `latest.json` will now point at v1.2.3 again.

⚠ **The Tauri auto-updater does not downgrade.** Users who already pulled
v1.2.4 need to install v1.2.3 by hand. Avoid this — smoke-test locally
before every tag push.

---

## Quick reference: "I want to..."

| Goal | Steps |
|---|---|
| Fix a bug, test locally | Edit code → Cmd-R in browser. No Tauri rebuild. |
| Ship that fix | PR → CI green → `scripts/bump-version.sh 1.x.y` (patch) → tag + push |
| Ship a feature | Same + minor bump (`1.x.0`) + update CHANGELOG |
| Push a beta build for testers | `git tag v1.x.0-beta.1` on `beta` + push |
| Roll a release back | Delete tag, re-flag previous as latest, ask user to reinstall |
| Rebuild + install Tauri locally | `cd web && npm run build && cd .. && uv run pyinstaller packaging/companion-bin.spec --noconfirm && cd tauri && cargo tauri build && cp -R src-tauri/target/release/bundle/macos/Companion.app /Applications/` |
| Restart server only | `pkill -9 -f companion-server && uv run companion-server` |
| Rebuild frontend only | `cd web && npm run build` — FastAPI serves the new bundles right away |

---

## Helper scripts (planned)

```
scripts/
  bump-version.sh   # bump Cargo.toml + tauri.conf.json + web/package.json
  release.sh        # bump + commit + tag + push
  test-release.sh   # download the latest DMG, install, smoke
  rollback.sh       # delete tag, re-flag previous as latest
```

Not all of these exist yet — see issue tracker for "release-tooling" label.
