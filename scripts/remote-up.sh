#!/usr/bin/env bash
# remote-up.sh — Provision a Cloudflare Tunnel for Companion (roadmap 5.2)
#
# Usage:
#   ./scripts/remote-up.sh <domain>            # full setup
#   ./scripts/remote-up.sh --dry-run <domain>  # validate inputs only, no external calls
#
# The tunnel will expose http://localhost:8082 at https://companion.<domain>.
# After the tunnel is up the script prints the Cloudflare Access policy to paste.

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COMPANION_PORT=8082
COMPANION_HOST="localhost"
HEALTHZ_TIMEOUT=30   # seconds to wait for HTTPS smoke-test
CF_CONFIG_DIR="${HOME}/.cloudflared"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { printf '\033[1;34m[remote-up]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[remote-up] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[remote-up] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat >&2 <<EOF
Usage: $0 [--dry-run] <domain>

  <domain>     Your Cloudflare-managed domain (e.g. example.com)
  --dry-run    Validate inputs and print what would happen — no external calls.

Examples:
  $0 mydomain.com
  $0 --dry-run staging.example.com

Requires:
  - cloudflared installed (brew install cloudflared  OR  apt install cloudflared)
  - An active Cloudflare account with <domain> in nameservers
  - Companion server running on port $COMPANION_PORT
EOF
    exit 1
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DRY_RUN=false
DOMAIN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage ;;
        -*) die "Unknown flag: $1" ;;
        *)
            if [[ -n "$DOMAIN" ]]; then
                die "Unexpected argument: $1"
            fi
            DOMAIN="$1"
            shift
            ;;
    esac
done

[[ -n "$DOMAIN" ]] || usage

# Basic domain sanity-check: must contain at least one dot
if [[ "$DOMAIN" != *.* ]]; then
    die "Domain must be a fully qualified domain name (e.g. example.com), got: $DOMAIN"
fi

TUNNEL_NAME="companion-$(hostname -s)"
TUNNEL_HOSTNAME="companion.${DOMAIN}"
PLIST_LABEL="com.companion.cloudflared"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
SYSTEMD_UNIT_PATH="${HOME}/.config/systemd/user/${PLIST_LABEL}.service"

# ---------------------------------------------------------------------------
# Dry-run short-circuit
# ---------------------------------------------------------------------------
if $DRY_RUN; then
    log "--- DRY-RUN MODE — no external calls will be made ---"
    log "domain        : $DOMAIN"
    log "tunnel name   : $TUNNEL_NAME"
    log "public URL    : https://${TUNNEL_HOSTNAME}"
    log "cloudflared   : $(command -v cloudflared 2>/dev/null || echo '(not found — install before real run)')"
    log "config file   : ${CF_CONFIG_DIR}/config.yml"
    log "Companion port: $COMPANION_PORT"
    log "--- Dry-run complete ---"
    exit 0
fi

# ---------------------------------------------------------------------------
# Pre-flight: cloudflared must be installed
# ---------------------------------------------------------------------------
if ! command -v cloudflared &>/dev/null; then
    die "cloudflared is not installed.
  macOS  : brew install cloudflared
  Linux  : apt install cloudflared  OR  https://pkg.cloudflare.com/"
fi

log "cloudflared found: $(cloudflared --version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# Pre-flight: Companion must be running
# ---------------------------------------------------------------------------
if ! curl -sf "http://${COMPANION_HOST}:${COMPANION_PORT}/healthz" &>/dev/null; then
    warn "Companion is not responding on http://${COMPANION_HOST}:${COMPANION_PORT}/healthz"
    warn "Start it with:  uv run fcc-server"
    die  "Companion must be running on port $COMPANION_PORT before provisioning the tunnel."
fi
log "Companion healthz OK on port $COMPANION_PORT"

# ---------------------------------------------------------------------------
# Cloudflare login (idempotent — skipped if cert already present)
# ---------------------------------------------------------------------------
if [[ ! -f "${CF_CONFIG_DIR}/cert.pem" ]]; then
    log "No Cloudflare certificate found — launching browser login..."
    cloudflared tunnel login
else
    log "Cloudflare certificate found, skipping login."
fi

# ---------------------------------------------------------------------------
# Tunnel creation (idempotent — reuse if already exists)
# ---------------------------------------------------------------------------
mkdir -p "$CF_CONFIG_DIR"

TUNNEL_ID=""

# Check if tunnel already exists
if cloudflared tunnel list --output json 2>/dev/null | grep -q "\"name\":\"${TUNNEL_NAME}\""; then
    log "Tunnel '$TUNNEL_NAME' already exists — reusing."
    TUNNEL_ID=$(cloudflared tunnel list --output json 2>/dev/null \
        | python3 -c "
import sys, json
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t.get('name') == '${TUNNEL_NAME}':
        print(t['id'])
        break
")
else
    log "Creating tunnel '$TUNNEL_NAME'..."
    TUNNEL_ID=$(cloudflared tunnel create "$TUNNEL_NAME" --output json 2>/dev/null \
        | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
fi

[[ -n "$TUNNEL_ID" ]] || die "Could not determine tunnel ID for '$TUNNEL_NAME'."
log "Tunnel ID: $TUNNEL_ID"

# ---------------------------------------------------------------------------
# Write ~/.cloudflared/config.yml
# ---------------------------------------------------------------------------
CONFIG_FILE="${CF_CONFIG_DIR}/config.yml"
log "Writing $CONFIG_FILE ..."

cat >"$CONFIG_FILE" <<YAML
tunnel: ${TUNNEL_ID}
credentials-file: ${CF_CONFIG_DIR}/${TUNNEL_ID}.json
ingress:
  - hostname: ${TUNNEL_HOSTNAME}
    service: http://localhost:${COMPANION_PORT}
  - service: http_status:404
YAML

log "Config written: $CONFIG_FILE"

# ---------------------------------------------------------------------------
# DNS route (idempotent — cloudflared handles duplicates gracefully)
# ---------------------------------------------------------------------------
log "Routing DNS: $TUNNEL_HOSTNAME -> $TUNNEL_NAME ..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$TUNNEL_HOSTNAME" || \
    warn "DNS route command returned non-zero — may already exist, continuing."

# ---------------------------------------------------------------------------
# Service installation: macOS launchd  OR  Linux systemd
# ---------------------------------------------------------------------------
case "$(uname -s)" in
    Darwin)
        log "Installing launchd service: $PLIST_LABEL ..."
        mkdir -p "$(dirname "$PLIST_PATH")"

        cat >"$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(command -v cloudflared)</string>
        <string>tunnel</string>
        <string>--config</string>
        <string>${CONFIG_FILE}</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/cloudflared-companion.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/cloudflared-companion.error.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
PLIST

        # Unload first if already loaded (idempotent)
        launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
        launchctl load -w "$PLIST_PATH"
        log "launchd service loaded."
        ;;

    Linux)
        log "Installing systemd user service: $PLIST_LABEL ..."
        mkdir -p "$(dirname "$SYSTEMD_UNIT_PATH")"

        cat >"$SYSTEMD_UNIT_PATH" <<UNIT
[Unit]
Description=Cloudflare Tunnel for Companion
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$(command -v cloudflared) tunnel --config ${CONFIG_FILE} run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
UNIT

        systemctl --user daemon-reload
        systemctl --user enable --now "${PLIST_LABEL}.service"
        log "systemd user service enabled and started."
        ;;

    *)
        warn "Unsupported OS: $(uname -s). Service not installed automatically."
        warn "Run manually: cloudflared tunnel --config ${CONFIG_FILE} run"
        ;;
esac

# ---------------------------------------------------------------------------
# Smoke test: wait up to HEALTHZ_TIMEOUT seconds for the public URL
# ---------------------------------------------------------------------------
log "Waiting for https://${TUNNEL_HOSTNAME}/healthz (up to ${HEALTHZ_TIMEOUT}s)..."

DEADLINE=$(( $(date +%s) + HEALTHZ_TIMEOUT ))
HEALTHY=false
while [[ $(date +%s) -lt $DEADLINE ]]; do
    if curl -sf --max-time 5 "https://${TUNNEL_HOSTNAME}/healthz" &>/dev/null; then
        HEALTHY=true
        break
    fi
    sleep 3
done

if $HEALTHY; then
    log "Smoke test passed — https://${TUNNEL_HOSTNAME}/healthz returned 200."
else
    warn "Smoke test timed out after ${HEALTHZ_TIMEOUT}s."
    warn "The tunnel may still be propagating. Try: curl https://${TUNNEL_HOSTNAME}/healthz"
fi

# ---------------------------------------------------------------------------
# Summary + Cloudflare Access policy instructions
# ---------------------------------------------------------------------------
cat <<SUMMARY

===============================================================
  Companion is now accessible at: https://${TUNNEL_HOSTNAME}
===============================================================

  Tunnel name : ${TUNNEL_NAME}
  Tunnel ID   : ${TUNNEL_ID}
  Config      : ${CONFIG_FILE}

---------------------------------------------------------------
  IMPORTANT: Gate this URL with Cloudflare Access
---------------------------------------------------------------

  To require a login before anyone can reach Companion:

  1. Go to https://one.dash.cloudflare.com/
  2. Navigate to: Access > Applications > Add an Application
  3. Choose "Self-hosted"
  4. Fill in:
       Application name : Companion
       Session Duration : 24 hours
       Subdomain        : companion
       Domain           : ${DOMAIN}
  5. Under "Policies", add a new policy:
       Policy name : Allow team
       Action      : Allow
       Rule        : Emails — enter the Google accounts / emails to permit
  6. Save & deploy.

  After enabling Access, set these env vars in your Companion .env:
    CF_ACCESS_AUD=<your-application-audience-tag>
    CF_ACCESS_TEAM=<your-team-name>   # e.g. "myteam" from myteam.cloudflareaccess.com

  The audience tag is shown on the Application overview page in the
  Cloudflare Zero Trust dashboard under "Application Audience (AUD) Tag".

  Companion will then accept either Bearer token OR a Cloudflare
  Access JWT (Cf-Access-Jwt-Assertion header), as configured in
  item 5.1 of the roadmap.

---------------------------------------------------------------
  Service logs
---------------------------------------------------------------
  macOS : tail -f /tmp/cloudflared-companion.log
  Linux : journalctl --user -u ${PLIST_LABEL} -f

SUMMARY
