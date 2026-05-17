"""Dependency injection for FastAPI."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from loguru import logger
from starlette.applications import Starlette

from config.settings import Settings
from config.settings import get_settings as _get_settings
from core.anthropic import get_user_facing_error_message
from providers.base import BaseProvider
from providers.exceptions import (
    AuthenticationError,
    ServiceUnavailableError,
    UnknownProviderTypeError,
)
from providers.registry import PROVIDER_DESCRIPTORS, ProviderRegistry

# Process-level cache: only for :func:`get_provider_for_type` / :func:`get_provider`
# when there is no ``Request``/``app`` (unit tests, scripts). HTTP handlers must pass
# ``app`` to :func:`resolve_provider` so the app-scoped registry is used.
_providers: dict[str, BaseProvider] = {}


def get_settings() -> Settings:
    """Return cached :class:`~config.settings.Settings` (FastAPI-friendly alias)."""
    return _get_settings()


def resolve_provider(
    provider_type: str,
    *,
    app: Starlette | None,
    settings: Settings,
) -> BaseProvider:
    """Resolve a provider using the app-scoped registry when ``app`` is set.

    When ``app`` is not ``None``, the app-owned :attr:`app.state.provider_registry`
    must exist (installed by :class:`~api.runtime.AppRuntime` during startup).
    Callers that construct a bare ``FastAPI`` without lifespan must set
    ``app.state.provider_registry`` explicitly.

    When ``app`` is ``None`` (no HTTP context), uses the process-level
    :data:`_providers` cache only.
    """
    if app is not None:
        reg = getattr(app.state, "provider_registry", None)
        if reg is None:
            raise ServiceUnavailableError(
                "Provider registry is not configured. Ensure AppRuntime startup ran "
                "or assign app.state.provider_registry for test apps."
            )
        return _resolve_with_registry(reg, provider_type, settings)
    return _resolve_with_registry(ProviderRegistry(_providers), provider_type, settings)


def _resolve_with_registry(
    registry: ProviderRegistry, provider_type: str, settings: Settings
) -> BaseProvider:
    should_log_init = not registry.is_cached(provider_type)
    try:
        provider = registry.get(provider_type, settings)
    except AuthenticationError as e:
        # Provider :class:`~providers.exceptions.AuthenticationError` messages are
        # curated configuration hints (env var names, docs links), not upstream noise.
        detail = str(e).strip() or get_user_facing_error_message(e)
        raise HTTPException(status_code=503, detail=detail) from e
    except UnknownProviderTypeError:
        logger.error(
            "Unknown provider_type: '{}'. Supported: {}",
            provider_type,
            ", ".join(f"'{key}'" for key in PROVIDER_DESCRIPTORS),
        )
        raise
    if should_log_init:
        logger.info("Provider initialized: {}", provider_type)
    return provider


def get_provider_for_type(provider_type: str) -> BaseProvider:
    """Get or create a provider in the process-level cache (no ``app``/Request).

    HTTP route handlers should call :func:`resolve_provider` with the active
    :attr:`request.app` (via :class:`~api.runtime.AppRuntime`) instead of this
    process-wide cache.
    """
    return resolve_provider(provider_type, app=None, settings=get_settings())


def require_api_key(
    request: Request, settings: Settings = Depends(get_settings)
) -> None:
    """Require a server API key (Anthropic-style).

    Checks `x-api-key` header or `Authorization: Bearer ...` against
    `Settings.anthropic_auth_token`. If `ANTHROPIC_AUTH_TOKEN` is empty, this is a no-op.
    """
    anthropic_auth_token = settings.anthropic_auth_token
    if not anthropic_auth_token:
        # No API key configured -> allow
        return

    header = (
        request.headers.get("x-api-key")
        or request.headers.get("authorization")
        or request.headers.get("anthropic-auth-token")
    )
    if not header:
        raise HTTPException(status_code=401, detail="Missing API key")

    # Support both raw key in X-API-Key and Bearer token in Authorization
    token = header
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1]

    # Strip anything after the first colon to handle tokens with appended model names
    if token and ":" in token:
        token = token.split(":", 1)[0]

    # Constant-time comparison to avoid leaking the configured token via
    # response-time differences on a per-byte mismatch (CWE-208).
    if not secrets.compare_digest(
        token.encode("utf-8"), anthropic_auth_token.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")


def get_provider() -> BaseProvider:
    """Get or create the default provider (``MODEL`` / ``provider_type``).

    Process-cache helper for scripts, unit tests, and non-FastAPI callers. HTTP
    handlers must use :func:`resolve_provider` with :attr:`request.app` so the
    app-scoped :class:`~providers.registry.ProviderRegistry` is used.
    """
    return get_provider_for_type(get_settings().provider_type)


async def cleanup_provider():
    """Cleanup all provider resources."""
    global _providers
    await ProviderRegistry(_providers).cleanup()
    _providers = {}
    logger.debug("Provider cleanup completed")


def _parse_companion_users(companion_users_csv: str) -> dict[str, str]:
    """Parse COMPANION_USERS CSV (``token=user_id,...``) into a mapping dict.

    Invalid entries (no ``=`` sign, empty token or user) are silently skipped.
    """
    mapping: dict[str, str] = {}
    for part in companion_users_csv.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        token, _, user = part.partition("=")
        token = token.strip()
        user = user.strip()
        if token and user:
            mapping[token] = user
    return mapping


def get_current_user_id(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    """Derive the caller's user_id from the request.

    Resolution order:
    1. ``x-companion-user`` header (explicit override — highest priority).
    2. Bearer token → user_id lookup via ``Settings.companion_users`` CSV.
    3. Fall back to ``"default"`` (single-user mode, backward-compatible).
    """
    # 1. Explicit header
    header_user = request.headers.get("x-companion-user", "").strip()
    if header_user:
        return header_user

    # 2. Bearer token → user lookup
    companion_users_csv = (settings.companion_users or "").strip()
    if companion_users_csv:
        token_map = _parse_companion_users(companion_users_csv)
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            bearer = auth_header.split(" ", 1)[1].strip()
            # Strip any trailing ":model" appended by some clients.
            if bearer and ":" in bearer:
                bearer = bearer.split(":", 1)[0]
            if bearer and bearer in token_map:
                return token_map[bearer]

    return "default"


# Convenience type alias for use in route signatures.
CurrentUserId = Annotated[str, Depends(get_current_user_id)]
