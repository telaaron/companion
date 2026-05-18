"""Tests for the auth layer: Bearer tokens + Cloudflare Access JWT."""

import base64
import time
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import Principal, _clear_jwks_cache, verify_cf_access_jwt
from api.dependencies import get_settings
from config.settings import Settings

app = create_app()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_rsa_key():
    """Return (private_key, public_key) RSA pair."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


def _int_to_base64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


def _make_jwks_dict(public_key, kid: str = "test-kid") -> dict:
    nums = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _int_to_base64url(nums.n),
                "e": _int_to_base64url(nums.e),
            }
        ]
    }


def _make_cf_token(
    private_key,
    audience: str,
    team: str,
    kid: str = "test-kid",
    exp_offset: int = 3600,
    email: str = "user@example.com",
) -> str:
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    payload = {
        "sub": "user-sub",
        "email": email,
        "aud": audience,
        "iss": f"https://{team}.cloudflareaccess.com",
        "iat": now,
        "exp": now + exp_offset,
    }
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# Existing Bearer tests (must keep passing)
# ---------------------------------------------------------------------------


def test_anthropic_auth_token_required_and_accepts_x_api_key():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "s3cr3t"
    app.dependency_overrides[get_settings] = lambda: settings

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with patch("api.routes.get_token_count", return_value=1):
        # No header -> 401
        r = client.post("/v1/messages/count_tokens", json=payload)
        assert r.status_code == 401

        # X-API-Key header -> 200
        r = client.post(
            "/v1/messages/count_tokens", json=payload, headers={"X-API-Key": "s3cr3t"}
        )
        assert r.status_code == 200
        assert r.json()["input_tokens"] == 1

    app.dependency_overrides.clear()


def test_anthropic_auth_token_accepts_bearer_authorization():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "b3artoken"
    app.dependency_overrides[get_settings] = lambda: settings

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with patch("api.routes.get_token_count", return_value=2):
        # Authorization Bearer -> 200
        r = client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={"Authorization": "Bearer b3artoken"},
        )
        assert r.status_code == 200
        assert r.json()["input_tokens"] == 2

    app.dependency_overrides.clear()


def test_anthropic_auth_token_applies_to_models_endpoint():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "models-token"
    app.dependency_overrides[get_settings] = lambda: settings

    r = client.get("/v1/models")
    assert r.status_code == 401

    r = client.get("/v1/models", headers={"X-API-Key": "models-token"})
    assert r.status_code == 200
    assert "data" in r.json()

    app.dependency_overrides.clear()


def test_root_get_requires_auth_but_root_probes_are_public():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "root-token"
    app.dependency_overrides[get_settings] = lambda: settings

    response = client.get("/")
    assert response.status_code == 401

    head = client.head("/")
    assert head.status_code == 204
    assert head.headers["Allow"] == "GET, HEAD, OPTIONS"

    options = client.options("/")
    assert options.status_code == 204
    assert options.headers["Allow"] == "GET, HEAD, OPTIONS"

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# CF Access JWT tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_cf_access_jwt_valid_token():
    """A well-formed, properly signed token returns claims."""
    priv, pub = _generate_rsa_key()
    audience = "test-aud"
    team = "myteam"
    jwks_dict = _make_jwks_dict(pub)
    token = _make_cf_token(priv, audience, team)

    _clear_jwks_cache(team)
    with patch(
        "api.auth._fetch_jwks", new=AsyncMock(return_value=_build_jwks(jwks_dict))
    ):
        claims = await verify_cf_access_jwt(token, audience, team)
    assert claims is not None
    assert claims["email"] == "user@example.com"
    assert claims["aud"] == audience


@pytest.mark.asyncio
async def test_verify_cf_access_jwt_tampered_signature_returns_none():
    """A token with a tampered signature returns None (not an exception)."""
    _priv, pub = _generate_rsa_key()
    other_priv, _ = _generate_rsa_key()
    audience = "test-aud"
    team = "myteam-tampered"
    jwks_dict = _make_jwks_dict(pub)  # JWKS has pub key for priv, not other_priv
    token = _make_cf_token(other_priv, audience, team)  # signed with wrong key

    _clear_jwks_cache(team)
    with patch(
        "api.auth._fetch_jwks", new=AsyncMock(return_value=_build_jwks(jwks_dict))
    ):
        claims = await verify_cf_access_jwt(token, audience, team)
    assert claims is None


@pytest.mark.asyncio
async def test_verify_cf_access_jwt_expired_token_returns_none():
    """An expired token returns None."""
    priv, pub = _generate_rsa_key()
    audience = "test-aud"
    team = "myteam-expired"
    jwks_dict = _make_jwks_dict(pub)
    token = _make_cf_token(priv, audience, team, exp_offset=-60)  # expired 60s ago

    _clear_jwks_cache(team)
    with patch(
        "api.auth._fetch_jwks", new=AsyncMock(return_value=_build_jwks(jwks_dict))
    ):
        claims = await verify_cf_access_jwt(token, audience, team)
    assert claims is None


@pytest.mark.asyncio
async def test_verify_cf_access_jwt_wrong_audience_returns_none():
    """A token with a mismatched audience returns None."""
    priv, pub = _generate_rsa_key()
    team = "myteam-aud"
    jwks_dict = _make_jwks_dict(pub)
    token = _make_cf_token(priv, "correct-aud", team)

    _clear_jwks_cache(team)
    with patch(
        "api.auth._fetch_jwks", new=AsyncMock(return_value=_build_jwks(jwks_dict))
    ):
        claims = await verify_cf_access_jwt(token, "wrong-aud", team)
    assert claims is None


@pytest.mark.asyncio
async def test_jwks_cache_hit():
    """JWKS is fetched once and cached on repeated calls."""
    priv, pub = _generate_rsa_key()
    audience = "cache-aud"
    team = "cache-team"
    jwks_dict = _make_jwks_dict(pub)
    token = _make_cf_token(priv, audience, team)

    _clear_jwks_cache(team)
    mock_fetch = AsyncMock(return_value=_build_jwks(jwks_dict))
    with patch("api.auth._fetch_jwks", new=mock_fetch):
        await verify_cf_access_jwt(token, audience, team)
        await verify_cf_access_jwt(token, audience, team)

    # _fetch_jwks should only be called once (cache hit on second call)
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_jwks_cache_miss_after_clear():
    """Clearing the cache causes a fresh fetch."""
    priv, pub = _generate_rsa_key()
    audience = "miss-aud"
    team = "miss-team"
    jwks_dict = _make_jwks_dict(pub)
    token = _make_cf_token(priv, audience, team)

    _clear_jwks_cache(team)
    mock_fetch = AsyncMock(return_value=_build_jwks(jwks_dict))
    with patch("api.auth._fetch_jwks", new=mock_fetch):
        await verify_cf_access_jwt(token, audience, team)
        _clear_jwks_cache(team)
        await verify_cf_access_jwt(token, audience, team)

    # Two fetches: one per call after clearing
    assert mock_fetch.call_count == 2


def test_cf_access_jwt_via_http_endpoint_valid():
    """An HTTP request with a valid CF Access JWT gets 200."""
    priv, pub = _generate_rsa_key()
    audience = "http-aud"
    team = "http-team"
    jwks_dict = _make_jwks_dict(pub)
    token = _make_cf_token(priv, audience, team)

    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = ""
    settings.cf_access_aud = audience
    settings.cf_access_team = team
    app.dependency_overrides[get_settings] = lambda: settings

    _clear_jwks_cache(team)
    with patch(
        "api.auth._fetch_jwks", new=AsyncMock(return_value=_build_jwks(jwks_dict))
    ):
        r = client.get("/v1/models", headers={"Cf-Access-Jwt-Assertion": token})
    assert r.status_code == 200

    app.dependency_overrides.clear()


def test_cf_access_jwt_via_http_endpoint_tampered():
    """An HTTP request with a tampered CF Access JWT gets 401."""
    _priv, pub = _generate_rsa_key()
    other_priv, _ = _generate_rsa_key()
    audience = "http-aud-bad"
    team = "http-team-bad"
    jwks_dict = _make_jwks_dict(pub)
    bad_token = _make_cf_token(other_priv, audience, team)

    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = ""
    settings.cf_access_aud = audience
    settings.cf_access_team = team
    app.dependency_overrides[get_settings] = lambda: settings

    _clear_jwks_cache(team)
    with patch(
        "api.auth._fetch_jwks", new=AsyncMock(return_value=_build_jwks(jwks_dict))
    ):
        r = client.get("/v1/models", headers={"Cf-Access-Jwt-Assertion": bad_token})
    assert r.status_code == 401

    app.dependency_overrides.clear()


def test_unauthenticated_request_returns_401_when_auth_configured():
    """No credentials + auth configured → 401."""
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "must-auth"
    app.dependency_overrides[get_settings] = lambda: settings

    r = client.get("/v1/models")
    assert r.status_code == 401

    app.dependency_overrides.clear()


def test_bearer_token_returns_200_when_auth_configured():
    """Valid Bearer token → 200."""
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "valid-token"
    app.dependency_overrides[get_settings] = lambda: settings

    r = client.get("/v1/models", headers={"Authorization": "Bearer valid-token"})
    assert r.status_code == 200

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Principal dataclass
# ---------------------------------------------------------------------------


def test_principal_bearer():
    p = Principal(kind="bearer", user_id="default", email=None)
    assert p.kind == "bearer"
    assert p.user_id == "default"
    assert p.email is None


def test_principal_cf_access():
    p = Principal(
        kind="cf_access", user_id="alice@example.com", email="alice@example.com"
    )
    assert p.kind == "cf_access"
    assert p.email == "alice@example.com"


# ---------------------------------------------------------------------------
# Helper: build PyJWKSet from a dict (for mocking _fetch_jwks)
# ---------------------------------------------------------------------------


def _build_jwks(jwks_dict: dict):
    from jwt import PyJWKSet

    return PyJWKSet.from_dict(jwks_dict)
