# 5.1 — Auth layer (Bearer + Cloudflare Access JWT)

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Every dashboard / API call requires either a Bearer token (existing
`ANTHROPIC_AUTH_TOKEN`) **or** a Cloudflare Access JWT
(`Cf-Access-Jwt-Assertion` header). Anonymous requests get 401. This is
the prerequisite for exposing Companion via Cloudflare Tunnel.

## Files

- `api/auth.py` (new):
  - `async verify_cf_access_jwt(token, audience, team) -> dict | None`
    using Cloudflare's JWKS at
    `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`.
    Cache JWKS for 1 h with `cachetools.TTLCache`.
  - `python-jose` (or `pyjwt`) for verification.
- `api/dependencies.py::require_api_key` — refactor: accept either
  Bearer (existing logic) or CF Access JWT. Reject if neither
  validates. Return a small `Principal` dataclass with `{kind, user_id,
  email | None}` for downstream use.
- `config/settings.py`:
  - `cf_access_aud` (str, default empty — only used when set).
  - `cf_access_team` (str, default empty).
  - `auth_required` (bool, default `true` when binding non-loopback).
- `api/runtime.py::startup` — log a loud warning if `auth_required=false`
  and bind host is not `127.0.0.1` or `::1`.
- `tests/api/test_auth.py` — coverage for: unauthenticated → 401, Bearer
  → 200, CF Access JWT valid → 200, CF Access JWT tampered → 401, JWKS
  cache hit/miss.

## Implementation plan

1. Implement `verify_cf_access_jwt`. Fetch JWKS, find the kid matching
   the token header, verify with the right algorithm
   (`RS256`/`ES256`), check `aud` matches `cf_access_aud`, check `exp`.
2. Update `require_api_key`. Pseudo:
   ```python
   async def require_api_key(request, settings):
       bearer = _extract_bearer(request)
       if bearer and constant_time_compare(bearer, settings.api_token):
           return Principal(kind="bearer", user_id="default", email=None)
       jwt_token = request.headers.get("cf-access-jwt-assertion")
       if jwt_token and settings.cf_access_aud and settings.cf_access_team:
           claims = await verify_cf_access_jwt(jwt_token, settings.cf_access_aud, settings.cf_access_team)
           if claims:
               return Principal(kind="cf_access", user_id=claims["email"], email=claims["email"])
       raise HTTPException(401, "unauthorized")
   ```
3. Make sure SSE endpoints (`/v1/jobs/{id}/events`) honor the same auth.
4. Tests use `httpx.MockTransport` to serve a fake JWKS + signed token.

## Acceptance

- `curl http://127.0.0.1:8082/v1/sessions` → 401.
- `curl -H "Authorization: Bearer $TOKEN" ...` → 200.
- With `CF_ACCESS_AUD` + `CF_ACCESS_TEAM` set, a request bearing a
  valid `Cf-Access-Jwt-Assertion` → 200; tampered → 401.
- `uv run pytest tests/api/test_auth.py` passes.

## Risks

- Locking yourself out on first deploy. Keep `--bind 127.0.0.1` working
  without any auth wiring. Default `auth_required=false` on loopback.
- JWKS rotation: cache TTL must be ≤ 1 h; force refresh on signature
  failure once before giving up.

## Verify

```bash
uv run pytest tests/api/test_auth.py -v
uv run pytest -v --tb=short
```
