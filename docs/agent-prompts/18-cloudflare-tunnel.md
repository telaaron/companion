# 5.2 — Cloudflare Tunnel quickstart

> Inherits shared context from [README.md](README.md). Read it first.
>
> **Depends on [17-auth-layer.md](17-auth-layer.md) being merged first.**

## Goal

`./scripts/remote-up.sh <domain>` provisions a Cloudflare Tunnel, prints
the public URL, and prints the exact Cloudflare Access policy to paste so
the URL is gated behind a login.

## Files

- `scripts/remote-up.sh` (new):
  - Verify `cloudflared` is installed (`brew install cloudflared` /
    `apt install cloudflared`).
  - Run `cloudflared tunnel login`.
  - `cloudflared tunnel create companion-$(hostname)`.
  - Write `~/.cloudflared/config.yml`:
    ```yaml
    tunnel: <tunnel-id>
    credentials-file: ~/.cloudflared/<tunnel-id>.json
    ingress:
      - hostname: companion.<domain>
        service: http://localhost:8082
      - service: http_status:404
    ```
  - `cloudflared tunnel route dns <tunnel-name> companion.<domain>`.
  - Install as launchd (macOS) / systemd (Linux) service.
  - Echo the URL + the recommended Cloudflare Access policy (allow only
    a chosen email / Google login).
- `docs/remote.md` — full walkthrough with screenshots.
- `tests/scripts/test_remote_up.bats` (optional) — bats-core sanity check
  that the script doesn't error on `--dry-run`.

## Implementation plan

1. Bash hardening: `set -euo pipefail`, validate the `<domain>` argument,
   `command -v cloudflared` check.
2. Idempotency: if a tunnel named `companion-$(hostname)` already
   exists, reuse it.
3. macOS launchd plist generation in `~/Library/LaunchAgents/`. Linux
   variant generates a systemd unit in `~/.config/systemd/user/`.
4. After install, smoke test: `curl https://companion.<domain>/healthz`
   should return 200 within 30 s.

## Acceptance

- Fresh Mac with `cloudflared` installed → `./scripts/remote-up.sh mydomain.com`
  → 90 s later `https://companion.mydomain.com` is reachable on cellular
  and gated by the configured Google login.
- Re-running the script doesn't duplicate the tunnel; it confirms the
  existing one is still good.

## Risks

- Requires a Cloudflare account + a domain in their nameservers. Refuse
  to run if `cloudflared tunnel list` doesn't auth.
- Companion must be running on `:8082` already — print a clear error
  if the local check fails.

## Verify

```bash
shellcheck scripts/remote-up.sh
./scripts/remote-up.sh --dry-run mydomain.com
```
