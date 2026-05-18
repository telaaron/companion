"""Cloudflare Access JWT verification for the auth layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cachetools import TTLCache
from jwt import PyJWKSet

# JWKS cache: keyed by team domain → PyJWKSet.  TTL = 3600 s.
_jwks_cache: TTLCache[str, PyJWKSet] = TTLCache(maxsize=16, ttl=3600)
_jwks_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _jwks_lock
    if _jwks_lock is None:
        _jwks_lock = asyncio.Lock()
    return _jwks_lock


def _jwks_url(team: str) -> str:
    return f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs"


async def _fetch_jwks(team: str) -> PyJWKSet:
    """Fetch JWKS from Cloudflare Access for the given team domain."""
    url = _jwks_url(team)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return PyJWKSet.from_dict(resp.json())


async def _get_jwks(team: str) -> PyJWKSet:
    """Return cached JWKS, fetching from Cloudflare when stale or missing."""
    async with _get_lock():
        cached = _jwks_cache.get(team)
        if cached is not None:
            return cached
        jwks = await _fetch_jwks(team)
        _jwks_cache[team] = jwks
        return jwks


def _clear_jwks_cache(team: str) -> None:
    """Evict a team's JWKS cache entry (forces refresh on next call)."""
    _jwks_cache.pop(team, None)


async def verify_cf_access_jwt(
    token: str,
    audience: str,
    team: str,
) -> dict[str, Any] | None:
    """Verify a Cloudflare Access JWT.

    Returns the decoded claims dict on success, or ``None`` on any failure.

    The JWKS is cached for up to 1 hour.  On a signature failure the cache is
    invalidated and one retry is attempted to handle key rotation.
    """
    for attempt in range(2):
        try:
            jwks = await _get_jwks(team)
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            signing_key = None
            for jwk in jwks.keys:
                if kid is None or jwk.key_id == kid:
                    signing_key = jwk.key
                    break

            if signing_key is None:
                if attempt == 0:
                    _clear_jwks_cache(team)
                    continue
                return None

            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=audience,
                options={"require": ["exp", "aud"]},
            )
            return claims

        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidSignatureError:
            if attempt == 0:
                _clear_jwks_cache(team)
                continue
            return None
        except jwt.InvalidTokenError, Exception:
            return None

    return None


@dataclass(frozen=True, slots=True)
class Principal:
    """Represents an authenticated caller."""

    kind: str  # "bearer" | "cf_access"
    user_id: str
    email: str | None


__all__ = ["Principal", "verify_cf_access_jwt"]
