# 5.3 — Tailscale + headless server image

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Ship a `docker-compose.yml` + Tailscale sidecar so an old Mac mini /
Raspberry Pi / NUC hosts Companion 24/7, accessible only on the user's
tailnet — no public URL, no Cloudflare account required.

## Files

- `deploy/docker/Dockerfile` — Python 3.14-slim + `uv` + copy repo +
  entrypoint `uv run fcc-server --bind 0.0.0.0`.
- `deploy/docker/docker-compose.yml`:
  ```yaml
  services:
    companion:
      build: .
      restart: unless-stopped
      env_file: .env
      networks: [tailnet]
      volumes:
        - companion-data:/data
    tailscale:
      image: tailscale/tailscale:latest
      environment:
        - TS_AUTHKEY=${TS_AUTHKEY}
        - TS_HOSTNAME=companion
        - TS_STATE_DIR=/var/lib/tailscale
        - TS_EXTRA_ARGS=--advertise-tags=tag:companion
      volumes:
        - tailscale-state:/var/lib/tailscale
      cap_add: [NET_ADMIN, NET_RAW]
      network_mode: service:companion
  networks:
    tailnet: {}
  volumes:
    companion-data: {}
    tailscale-state: {}
  ```
- `deploy/README.md` — three home-lab recipes:
  - macOS mini via launchd (no Docker).
  - Raspberry Pi via Docker Compose (above).
  - NUC via Docker Compose.
- `deploy/launchd/com.companion.server.plist` — for the macOS recipe.

## Implementation plan

1. Dockerfile: multi-stage if needed to keep the final image < 400 MB.
   Final layer copies only `pyproject.toml`, `uv.lock`, `api/`,
   `cli/`, `config/`, `core/`, `messaging/`, and runs `uv sync --frozen`.
2. Tailscale sidecar pattern: Companion container has no networking of
   its own; it shares the network namespace of the Tailscale sidecar
   (so `tailscale ip` resolves to the host).
3. `.env.example` documents: `TS_AUTHKEY`, `ANTHROPIC_AUTH_TOKEN`,
   provider keys, `MEMORY_INDEX_PATHS=/data/notes`.
4. Compose health check on the companion service hits `/healthz`.
5. README explains: install Tailscale on phone → ACL allow tag:companion
   from your devices → `http://companion.<tailnet>:8082/ui/`.

## Acceptance

- `docker compose up -d` on a fresh Pi → laptop on cellular reaches
  `http://companion.<tailnet>:8082/ui/` within 60 s.
- No Cloudflare account or domain involved.
- Restart of the host brings Companion back automatically.

## Risks

- TS_AUTHKEY rotation: document expiry + how to regenerate.
- Pi 4 with 4 GB RAM is the lower bound for comfortable use — note in
  the README.

## Verify

```bash
docker compose -f deploy/docker/docker-compose.yml config
hadolint deploy/docker/Dockerfile
```
