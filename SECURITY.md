# Policy

Thanks for helping keep Companion users safe. This document explains how to
report an issue and what to expect in return.

## Supported versions

Companion follows semantic versioning. We patch issues in the latest minor
release line; older lines are best-effort.

| Version | Status |
|---|---|
| 1.1.x | Active — fixes shipped |
| 1.0.x | End of life — please upgrade |
| < 1.0 | Unsupported |

The auto-updater inside Companion delivers fixes within minutes of a release
going live. If you self-build from source, follow `main`.

## How to report

**Please use a private channel, not a public issue.**

1. **GitHub private advisory** (preferred) —
   <https://github.com/telaaron/companion/security/advisories/new>
2. **Email** — see contact section below

Helpful info to include:

- Companion version + OS
- Steps to reproduce
- Impact you observed
- Optional: proof-of-concept
- Disclosure timeline you have in mind

## Response timeline

- **24h** — we acknowledge receipt
- **72h** — first triage + severity assessment
- **7 days** — fix plan or follow-up questions
- **30 days** — patched release ships, advisory published, credit given
  (with your permission)

If an issue is being actively used in the wild we move faster.

## Scope

In scope:

- Companion server (`api/`, `core/`, `providers/`, `cli/`)
- Tauri desktop shell (`tauri/`)
- SvelteKit UI (`web/`)
- Frozen-binary packaging (`packaging/`)
- The auto-updater chain (signing key handling, manifest fetch, install path)

Out of scope:

- Issues in unmodified upstream dependencies — please report those to the
  dependency maintainer; we will pull in their fix once available
- Reports that require local root or the user voluntarily handing over
  their API tokens
- Brute-force or DoS against the local-only proxy (`127.0.0.1:808x`) when
  exposed to a network the user controls

## Coordinated disclosure

We use a **30-day private window** by default. After a fix ships we publish
a GitHub advisory crediting the reporter (opt-in). If you need a longer
embargo for vendor coordination, tell us in the initial report.

## Recognition

There is no paid bounty program. We thank you publicly in release notes and
on the contributors list (opt-in).

## Cryptographic notes

- The auto-updater uses **minisign** with the public key embedded in
  `tauri/src-tauri/tauri.conf.json`. The matching private key is held only
  by the maintainer and stored in the CI runner as the
  `TAURI_SIGNING_PRIVATE_KEY` secret.
- Compromise of that key would let a bad actor push a malicious update to
  every installed Companion app. If you suspect compromise, please report
  it as a critical issue.

## Contact

- **Private advisory**: <https://github.com/telaaron/companion/security/advisories/new>
- **Email**: `admin@must-seen.com`
  Subject line: `[security] Companion`
- **PGP**: not yet published. Email first, key exchange on request.

We aim to reply within one business day.
