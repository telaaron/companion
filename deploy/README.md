# Companion — Home-Lab Deployment Guide

Three recipes for running Companion 24/7 on dedicated hardware, accessible
only on your Tailscale tailnet — no public URL, no Cloudflare account required.

---

## Prerequisites (all recipes)

1. **Tailscale account** — free tier is fine.
   [https://login.tailscale.com/start](https://login.tailscale.com/start)
2. **Auth key** — generate a reusable ephemeral key tagged for Companion:
   ```
   tailscale keys generate --ephemeral --reusable --tags=tag:companion
   ```
   Save the key (`tskey-auth-…`). Keys expire — see [Key rotation](#key-rotation).
3. **ACL rule** — in the Tailscale admin console add:
   ```json
   {
     "action": "accept",
     "src": ["autogroup:member"],
     "dst": ["tag:companion:8082"]
   }
   ```
   This lets every device on your tailnet reach Companion's API port.

---

## Recipe A — macOS Mac mini (launchd, no Docker)

**Requirements:** macOS 13+, Tailscale desktop app installed, `uv` installed.

### 1. Clone and configure

```bash
git clone https://github.com/telaaron/companion.git /opt/companion
cd /opt/companion
cp deploy/docker/.env.example /opt/companion/.env
# Edit .env — fill in ANTHROPIC_AUTH_TOKEN and any provider API keys
nano /opt/companion/.env
```

### 2. Install dependencies

```bash
cd /opt/companion
uv sync --frozen
```

### 3. Install the launchd service

```bash
# Edit WorkingDirectory and EnvironmentVariables in the plist first:
nano deploy/launchd/com.companion.server.plist

cp deploy/launchd/com.companion.server.plist \
   ~/Library/LaunchAgents/com.companion.server.plist

launchctl load -w ~/Library/LaunchAgents/com.companion.server.plist
```

### 4. Verify

```bash
launchctl list | grep companion
curl http://localhost:8082/health
```

Open on any tailnet device: `http://companion.<tailnet-name>.ts.net:8082/ui/`

### Stopping / uninstalling

```bash
launchctl unload -w ~/Library/LaunchAgents/com.companion.server.plist
rm ~/Library/LaunchAgents/com.companion.server.plist
```

---

## Recipe B — Raspberry Pi (Docker Compose)

**Requirements:** Raspberry Pi 4 with 4 GB RAM minimum (8 GB recommended),
Raspberry Pi OS Bookworm (64-bit), Docker + Docker Compose v2.

> **Note:** Python 3.14 binaries for ARM64 are available in the official Docker
> image (`python:3.14-slim`). The build may take 5–10 minutes on a Pi 4.

### 1. Clone and configure

```bash
git clone https://github.com/telaaron/companion.git /opt/companion
cd /opt/companion/deploy/docker
cp .env.example .env
# Edit .env — set TS_AUTHKEY, ANTHROPIC_AUTH_TOKEN, and provider keys
nano .env
```

### 2. Start

```bash
docker compose up -d
```

Docker Compose will:
- Build the Companion image (Python 3.14-slim + uv + dependencies).
- Start a Tailscale sidecar that shares Companion's network namespace.
- Register on your tailnet as `companion`.

### 3. Verify

```bash
# Check both containers are healthy
docker compose ps

# View logs
docker compose logs -f tailscale   # watch for "Tailscale up"
docker compose logs -f companion   # watch for "Uvicorn running"

# From a laptop on the same tailnet
curl http://companion.<tailnet-name>.ts.net:8082/health
```

Open the UI: `http://companion.<tailnet-name>.ts.net:8082/ui/`

### Updating Companion

```bash
cd /opt/companion
git pull
docker compose build --pull companion
docker compose up -d
```

---

## Recipe C — Intel NUC (Docker Compose)

Identical to Recipe B. The NUC's x86-64 architecture builds faster
(typically 1–2 minutes). Use the same steps from Recipe B.

**Recommended specs:** Intel NUC 11+ with 8 GB RAM, 120 GB SSD.

---

## Data persistence

Both the Docker recipes mount two named volumes:

| Volume | Contents |
|--------|----------|
| `companion-data` | SQLite database, session history, memory index |
| `tailscale-state` | Tailscale node key (survives restarts without re-auth) |

To back up your data:

```bash
docker run --rm \
  -v companion_companion-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/companion-data-$(date +%Y%m%d).tar.gz /data
```

---

## Key rotation

Tailscale auth keys expire. When your key expires:

1. Generate a new key in the Tailscale admin console.
2. Update `TS_AUTHKEY` in your `.env` file.
3. Restart the Tailscale container:
   ```bash
   docker compose restart tailscale
   ```
   Or, for launchd (macOS), no action needed — Tailscale desktop manages its
   own auth separately from the server.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `http://companion.…:8082` unreachable | Check `tailscale status` on host; verify ACL allows `tag:companion:8082` |
| `docker compose up` fails on Pi | Ensure Docker Compose v2 (`docker compose version`); update if < 2.20 |
| Companion crashes on startup | Run `docker compose logs companion`; check `.env` for missing required vars |
| Auth key expired | Rotate key — see [Key rotation](#key-rotation) |
| High memory on Pi 4 | Disable optional extras (voice, RAG) in `.env`; use a Pi with 8 GB |
