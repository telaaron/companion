"""Tests for the Imagine image-generation tool.

Covers the provider-name normalization bug (settings store ``openrouter``
but the dispatch keyed on ``open_router``) and the OpenRouter
chat-completions image-gen request/response contract.
"""

from __future__ import annotations

import base64
import json
import tempfile
from typing import Any

import pytest

from api.agent.extras import imagine as ig
from core.tools.workspace import Workspace

# 1x1 transparent PNG.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()


def test_normalize_provider_aliases() -> None:
    assert ig._normalize_provider("openrouter") == "open_router"
    assert ig._normalize_provider("OpenRouter") == "open_router"
    assert ig._normalize_provider("deep_seek") == "deepseek"
    assert ig._normalize_provider("open_router") == "open_router"
    assert ig._normalize_provider("custom") == "custom"


def test_resolve_api_key_via_openrouter_alias() -> None:
    class _S:
        image_gen_api_key = ""
        open_router_api_key = "sk-or-test"
        deepseek_api_key = ""
        anthropic_auth_token = ""

    # Provider must already be normalized when key lookup runs.
    assert ig._resolve_api_key(_S(), "open_router") == "sk-or-test"


def test_resolve_api_key_prefers_explicit_key() -> None:
    class _S:
        image_gen_api_key = "sk-explicit"
        open_router_api_key = "sk-or-test"
        deepseek_api_key = ""
        anthropic_auth_token = ""

    assert ig._resolve_api_key(_S(), "open_router") == "sk-explicit"


@pytest.mark.asyncio
async def test_decode_image_ref_data_url() -> None:
    out = await ig._decode_image_ref(f"data:image/png;base64,{_PNG_B64}")
    assert out == _PNG_BYTES


@pytest.mark.asyncio
async def test_decode_image_ref_empty_raises() -> None:
    with pytest.raises(RuntimeError):
        await ig._decode_image_ref("")


@pytest.mark.asyncio
async def test_execute_openrouter_end_to_end(monkeypatch: Any, tmp_path: Any) -> None:
    """openrouter alias resolves the key and parses chat-completions output."""

    class _S:
        image_gen_provider = "openrouter"  # alias, not open_router
        image_gen_model = "google/gemini-2.5-flash-image-preview"
        image_gen_size = "1024x1024"
        image_gen_api_key = ""
        open_router_api_key = "sk-or-test"
        deepseek_api_key = ""
        anthropic_auth_token = ""

    monkeypatch.setattr("config.settings.get_settings", lambda: _S())
    monkeypatch.setattr(ig, "_IMAGE_CACHE_DIR", tmp_path / "images")

    captured: dict[str, Any] = {}

    async def _fake_generate(provider, model, prompt, size, api_key):
        captured.update(provider=provider, model=model, api_key=api_key, prompt=prompt)
        return _PNG_BYTES

    monkeypatch.setattr(ig, "_generate_image", _fake_generate)

    ws = Workspace.create(str(tmp_path))
    result = await ig.execute({"prompt": "violet logo"}, ws)

    assert result.is_error is False
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    assert payload["status"] == "ok"
    assert payload["image_url"].startswith("/v1/image/")
    # Alias normalized before dispatch + key resolved from open_router_api_key.
    assert captured["provider"] == "open_router"
    assert captured["api_key"] == "sk-or-test"
    # Image actually written to cache dir.
    written = list((tmp_path / "images").glob("*.png"))
    assert len(written) == 1
    assert written[0].read_bytes() == _PNG_BYTES


@pytest.mark.asyncio
async def test_execute_missing_key_errors(monkeypatch: Any, tmp_path: Any) -> None:
    class _S:
        image_gen_provider = "openrouter"
        image_gen_model = ""
        image_gen_size = ""
        image_gen_api_key = ""
        open_router_api_key = ""
        deepseek_api_key = ""
        anthropic_auth_token = ""

    monkeypatch.setattr("config.settings.get_settings", lambda: _S())
    ws = Workspace.create(str(tmp_path))
    result = await ig.execute({"prompt": "x"}, ws)
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "No API key" in result.content


@pytest.mark.asyncio
async def test_execute_empty_prompt_errors(monkeypatch: Any) -> None:
    ws = Workspace.create(tempfile.mkdtemp())
    result = await ig.execute({"prompt": "  "}, ws)
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "required" in result.content
