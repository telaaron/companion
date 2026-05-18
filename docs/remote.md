# Remote Access via Cloudflare Tunnel

This guide shows how to expose your local Companion server to the internet
through a Cloudflare Tunnel so you can reach it from a phone, a browser on
another machine, or share it with a small team — all without opening firewall
ports or setting up a VPN.

**Before you start:**
- Companion must be installed and running on the machine that will serve as the
  host (port `8082` by default).
- You need a Cloudflare account and a domain whose nameservers point at
  Cloudflare (free tier is enough).
- `cloudflared` must be installed on the host.

---

## 1. Install cloudflared

### macOS
```bash
brew install cloudflared
```

### Debian / Ubuntu
```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
    sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
    https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | \
    sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared
```

### Other Linux / manual
Download the latest binary from
https://github.com/cloudflare/cloudflared/releases and place it on your `$PATH`.

Verify the install:
```bash
cloudflared --version
```

---

## 2. Run the quickstart script

Make sure Companion is already running (`uv run fcc-server`), then:

```bash
./scripts/remote-up.sh yourdomain.com
```

The script will:

1. Open a browser window to authenticate with Cloudflare (first run only).
2. Create a tunnel named `companion-<hostname>` (or reuse an existing one).
3. Write `~/.cloudflared/config.yml` pointing the tunnel at `localhost:8082`.
4. Register a DNS record: `companion.yourdomain.com → tunnel`.
5. Install and start a background service (launchd on macOS, systemd on Linux).
6. Wait up to 30 seconds for `https://companion.yourdomain.com/healthz` to
   return HTTP 200.
7. Print the public URL and a step-by-step guide for enabling Cloudflare Access.

### Dry-run mode

To validate arguments without making any external calls:

```bash
./scripts/remote-up.sh --dry-run yourdomain.com
```

### Re-running is safe

If the tunnel already exists the script reuses it — it will not create a
duplicate. It will rewrite the config file and restart the service.

---

## 3. Restrict access with Cloudflare Access

The tunnel makes Companion reachable from the internet. Without Access anyone
who discovers the URL could reach it (though your Bearer token still applies if
set). The recommended setup is:

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) →
   **Access** → **Applications** → **Add an Application**.
2. Choose **Self-hosted**.
3. Fill in:
   - **Application name:** Companion
   - **Subdomain:** `companion` / **Domain:** `yourdomain.com`
   - **Session duration:** 24 h (adjust to taste)
4. Under **Policies**, add a policy:
   - **Action:** Allow
   - **Rule:** Emails — list the Google (or other IdP) accounts to permit.
5. Save & deploy.

After deploying Access, copy the **Application Audience (AUD) Tag** from the
application overview page. Then add to your `.env`:

```dotenv
CF_ACCESS_AUD=<paste-aud-tag-here>
CF_ACCESS_TEAM=<your-team-name>   # e.g. "myteam" from myteam.cloudflareaccess.com
```

Restart Companion (`uv run fcc-server`). Companion will now accept both:

- `Authorization: Bearer <your-token>` (existing behaviour), **and**
- `Cf-Access-Jwt-Assertion: <jwt>` issued by Cloudflare Access (item 5.1).

Unauthenticated requests from the public internet receive `401 Unauthorized`.

---

## 4. Verify the setup

```bash
# Health check through the tunnel
curl https://companion.yourdomain.com/healthz

# Should return 401 without credentials when auth is enabled
curl https://companion.yourdomain.com/v1/sessions

# Should return 200 with your token
curl -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
     https://companion.yourdomain.com/v1/sessions
```

---

## 5. Service management

### macOS (launchd)

| Action | Command |
|--------|---------|
| View status | `launchctl list \| grep cloudflared` |
| View logs | `tail -f /tmp/cloudflared-companion.log` |
| Restart | `launchctl unload -w ~/Library/LaunchAgents/com.companion.cloudflared.plist && launchctl load -w ~/Library/LaunchAgents/com.companion.cloudflared.plist` |
| Remove | `launchctl unload -w ~/Library/LaunchAgents/com.companion.cloudflared.plist && rm ~/Library/LaunchAgents/com.companion.cloudflared.plist` |

### Linux (systemd)

| Action | Command |
|--------|---------|
| View status | `systemctl --user status com.companion.cloudflared` |
| View logs | `journalctl --user -u com.companion.cloudflared -f` |
| Restart | `systemctl --user restart com.companion.cloudflared` |
| Remove | `systemctl --user disable --now com.companion.cloudflared && rm ~/.config/systemd/user/com.companion.cloudflared.service` |

---

## 6. Updating the tunnel

If you change your domain or move the tunnel to a new machine:

```bash
# Remove the old tunnel
cloudflared tunnel delete companion-<old-hostname>

# Re-run the script on the new host
./scripts/remote-up.sh yourdomain.com
```

---

## 7. Troubleshooting

**Smoke test times out**

DNS propagation can take a few minutes. Wait and retry:
```bash
curl https://companion.yourdomain.com/healthz
```

**"cloudflared: command not found"**

Install cloudflared (see step 1 above) and ensure it is on your `$PATH`.

**"Companion is not responding on port 8082"**

Start the server first: `uv run fcc-server`

**Access policy not working / still getting 401**

- Confirm `CF_ACCESS_AUD` and `CF_ACCESS_TEAM` are set in your `.env`.
- Restart Companion after editing `.env`.
- Check that the AUD tag was copied from the correct application in the Zero
  Trust dashboard.

**Tunnel shows as "healthy" in dashboard but URL returns 502**

Companion may have stopped. Check `curl http://localhost:8082/healthz` on the
host and restart if needed.

---

## 8. Security notes

- Cloudflare Access terminates TLS and enforces your login policy at the edge.
  Traffic between Cloudflare and your host travels through the encrypted tunnel.
- Keep `ANTHROPIC_AUTH_TOKEN` set even with Access enabled — it provides a
  second authentication factor for API clients that bypass the browser flow.
- The `CF_ACCESS_AUD` value is not secret, but `ANTHROPIC_AUTH_TOKEN` is —
  never commit it to version control.
- By default, Companion enforces auth on non-loopback binds. Do not set
  `AUTH_REQUIRED=false` in production.
