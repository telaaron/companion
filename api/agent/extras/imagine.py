"""Imagine tool — generate images via the configured image-gen provider.

Reads IMAGE_GEN_PROVIDER / IMAGE_GEN_MODEL / IMAGE_GEN_SIZE from
settings and calls the matching API. Defaults to OpenRouter with
``black-forest-labs/flux-1-schnell``. Images are cached to
``~/.cache/companion/images/`` and served via ``GET /v1/image/{name}``.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

import httpx

from core.tools.result import ToolResult
from core.tools.workspace import Workspace

_IMAGE_CACHE_DIR = Path.home() / ".cache" / "companion" / "images"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    from config.settings import get_settings

    settings = get_settings()
    prompt = (input_data.get("prompt") or "").strip()
    if not prompt:
        return ToolResult(content="Error: 'prompt' is required", is_error=True)

    provider = _normalize_provider(settings.image_gen_provider or "open_router")
    model = settings.image_gen_model or "black-forest-labs/flux-1-schnell"
    size = settings.image_gen_size or "1024x1024"

    api_key = _resolve_api_key(settings, provider)
    if not api_key:
        env_hint = _key_env_hint(provider)
        return ToolResult(
            content=(
                f"Error: No API key for image gen provider '{provider}'. "
                f"Set IMAGE_GEN_API_KEY or {env_hint}."
            ),
            is_error=True,
        )

    try:
        image_data = await _generate_image(provider, model, prompt, size, api_key)
    except Exception as exc:
        return ToolResult(content=f"Image generation failed: {exc}", is_error=True)

    _IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:12]}.png"
    filepath = _IMAGE_CACHE_DIR / filename
    filepath.write_bytes(image_data)

    result = json.dumps(
        {
            "status": "ok",
            "image_url": f"/v1/image/{filename}",
            "prompt": prompt[:200],
            "size": size,
        }
    )
    return ToolResult(
        content=result, metadata={"image_file": str(filepath), "prompt": prompt}
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _normalize_provider(provider: str) -> str:
    """Canonicalize provider aliases to the internal snake_case form.

    Settings/UI store ``openrouter``; the rest of this module keys on
    ``open_router``. Without this, key lookup and dispatch silently miss.
    """
    aliases = {
        "openrouter": "open_router",
        "deep_seek": "deepseek",
    }
    p = provider.strip().lower()
    return aliases.get(p, p)


def _resolve_api_key(settings: Any, provider: str) -> str:
    """Return the best available API key for the given image-gen provider."""
    key = settings.image_gen_api_key
    if key:
        return key
    # Fall back to the provider's native chat API key.
    provider_keys: dict[str, str] = {
        "open_router": getattr(settings, "open_router_api_key", ""),
        "deepseek": getattr(settings, "deepseek_api_key", ""),
        "anthropic": getattr(settings, "anthropic_auth_token", ""),
    }
    return provider_keys.get(provider, "")


def _key_env_hint(provider: str) -> str:
    hints: dict[str, str] = {
        "open_router": "OPENROUTER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "anthropic": "ANTHROPIC_AUTH_TOKEN",
    }
    return hints.get(provider, f"{provider.upper()}_API_KEY")


async def _generate_image(
    provider: str, model: str, prompt: str, size: str, api_key: str
) -> bytes:
    if provider == "open_router":
        return await _openrouter_generate(model, prompt, size, api_key)
    # Generic OpenAI-compatible fallback.
    return await _generic_generate(provider, model, prompt, size, api_key)


async def _openrouter_generate(
    model: str, prompt: str, size: str, api_key: str
) -> bytes:
    # OpenRouter generates images through the chat-completions API: send the
    # prompt as a user message and request the ``image`` output modality. The
    # generated image comes back as a data URL on the assistant message.
    del size  # OpenRouter image models size themselves; kept for signature parity
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenRouter returned {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()

    choices: list[dict[str, Any]] = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices")
    message = choices[0].get("message") or {}
    images: list[dict[str, Any]] = message.get("images") or []
    if not images:
        raise RuntimeError("OpenRouter returned no images")

    url = (images[0].get("image_url") or {}).get("url", "")
    return await _decode_image_ref(url)


async def _decode_image_ref(url: str) -> bytes:
    """Decode a data: URL or fetch an http(s) image URL into raw bytes."""
    if not url:
        raise RuntimeError("Empty image reference in response")
    if url.startswith("data:"):
        _, _, b64 = url.partition(",")
        return base64.b64decode(b64)
    async with httpx.AsyncClient(timeout=30.0) as client:
        img = await client.get(url)
        if img.status_code != 200:
            raise RuntimeError(f"Image download failed: {img.status_code}")
        return img.content


async def _generic_generate(
    provider: str, model: str, prompt: str, size: str, api_key: str
) -> bytes:
    base_url = provider if provider.startswith("http") else ""
    if not base_url:
        raise RuntimeError(
            f"Unsupported image gen provider '{provider}'. "
            "Set IMAGE_GEN_BASE_URL for custom endpoints."
        )

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "response_format": "b64_json",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(base_url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Provider returned {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()

    images: list[dict[str, Any]] = data.get("data") or []
    if not images:
        raise RuntimeError("No images returned")

    result = images[0]
    if "b64_json" in result:
        return base64.b64decode(result["b64_json"])
    if "url" in result:
        async with httpx.AsyncClient(timeout=30.0) as client:
            img = await client.get(result["url"])
            if img.status_code != 200:
                raise RuntimeError(f"Image download failed: {img.status_code}")
            return img.content
    raise RuntimeError(f"Unexpected image response: {sorted(result.keys())}")
