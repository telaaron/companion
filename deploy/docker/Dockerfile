# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Stage 1 — build: install dependencies with uv into a venv
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy only the files needed to resolve + install dependencies first
# (cache layer: only re-runs when pyproject.toml / uv.lock change)
COPY pyproject.toml uv.lock ./

# Sync dependencies into /app/.venv (no editable install of the project yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# ---------------------------------------------------------------------------
# Stage 2 — final image
# ---------------------------------------------------------------------------
FROM python:3.14-slim

LABEL org.opencontainers.image.title="Companion" \
      org.opencontainers.image.description="Companion AI server — Tailscale headless deployment" \
      org.opencontainers.image.source="https://github.com/telaaron/companion"

# Runtime system packages (minimal)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv in final image (needed for `uv run`)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy the pre-built venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source (only the packages that ship in the server)
COPY pyproject.toml uv.lock ./
COPY api/       ./api/
COPY cli/       ./cli/
COPY config/    ./config/
COPY core/      ./core/
COPY messaging/ ./messaging/

# Persist SQLite DB + memory index outside the container
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    # Server defaults — override via .env / environment
    FCC_HOST=0.0.0.0 \
    FCC_PORT=8082 \
    COMPANION_DATA_DIR=/data

EXPOSE 8082

# Health check — lightweight GET against the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8082/health || exit 1

ENTRYPOINT ["uv", "run", "fcc-server"]
