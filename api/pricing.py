"""Pricing table for cost estimation.

Numbers are USD per 1M tokens unless noted otherwise. Image-generation cost
is per-image. Sourced from each provider's public pricing page (snapshotted
2026-04). Update via env var ``FCC_PRICING_OVERRIDES_JSON`` for custom or
fresher numbers without redeploying. Override format:

    {
        "deepseek/deepseek-v4-pro":   {"input_per_mtok": 0.55, "output_per_mtok": 2.19},
        "openrouter/google/gemini-2.5-flash-image":  {"per_image_usd": 0.039}
    }
"""

from __future__ import annotations

import json
import os
from typing import Any

from loguru import logger

# Defaults — best-effort. None of these is load-bearing for correctness; they
# just feed the dashboard. Wrong numbers degrade UX, not behavior.
_TOKEN_PRICES: dict[str, dict[str, float]] = {
    # DeepSeek native (Anthropic-compat endpoint)
    "deepseek/deepseek-v4-pro": {"input_per_mtok": 0.55, "output_per_mtok": 2.19},
    "deepseek/deepseek-v4-flash": {"input_per_mtok": 0.27, "output_per_mtok": 1.10},
    "deepseek/deepseek-chat": {"input_per_mtok": 0.27, "output_per_mtok": 1.10},
    # OpenRouter — varies wildly by underlying model. Conservative averages.
    "openrouter/anthropic/claude-sonnet-4.5": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
    },
    "openrouter/anthropic/claude-opus-4": {
        "input_per_mtok": 15.00,
        "output_per_mtok": 75.00,
    },
    "openrouter/deepseek/deepseek-r1-0528:free": {
        "input_per_mtok": 0.0,
        "output_per_mtok": 0.0,
    },
    "openrouter/google/gemma-2-9b-it:free": {
        "input_per_mtok": 0.0,
        "output_per_mtok": 0.0,
    },
    # Kimi
    "kimi/kimi-k2.5": {"input_per_mtok": 0.30, "output_per_mtok": 2.50},
    # NVIDIA NIM — most checkpoints are free with a key; cost ~0.
    "nvidia_nim/z-ai/glm4.7": {"input_per_mtok": 0.0, "output_per_mtok": 0.0},
    # Vision (Gemini OpenAI-compat endpoint, used as DeepSeek vision fallback)
    "gemini/gemini-flash-latest": {"input_per_mtok": 0.075, "output_per_mtok": 0.30},
    "gemini/gemini-2.5-flash": {"input_per_mtok": 0.075, "output_per_mtok": 0.30},
    "gemini/gemini-2.5-pro": {"input_per_mtok": 1.25, "output_per_mtok": 5.00},
}

_IMAGE_PRICES: dict[str, float] = {
    # OpenRouter image models — per-image flat fee
    "openrouter/google/gemini-2.5-flash-image": 0.039,
    "openrouter/google/gemini-3.1-flash-image-preview": 0.039,
    "openrouter/openai/gpt-5-image": 0.080,
    "openrouter/openai/gpt-5-image-mini": 0.020,
    # Gemini direct
    "gemini/gemini-2.5-flash-image": 0.039,
}


def _load_overrides() -> dict[str, dict[str, float]]:
    raw = os.environ.get("FCC_PRICING_OVERRIDES_JSON", "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            return {}
        return loaded
    except (ValueError, TypeError) as exc:
        logger.warning("PRICING: ignoring invalid FCC_PRICING_OVERRIDES_JSON ({})", exc)
        return {}


def _normalize_key(provider: str, model: str) -> str:
    if "/" in model:
        # avoid double-prefixing for nested ids like ``z-ai/glm4.7``
        return f"{provider}/{model}"
    return f"{provider}/{model}"


def estimate_token_cost(
    *, provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    """Return estimated USD cost for the given token counts.

    Returns 0.0 when no pricing entry is known so the dashboard still records
    the event (just with cost=0). The UI surfaces "no pricing" rows so users
    know to override.
    """
    key = _normalize_key(provider, model)
    overrides = _load_overrides()
    entry = overrides.get(key) or _TOKEN_PRICES.get(key)
    if entry is None:
        return 0.0
    in_rate = float(entry.get("input_per_mtok", 0.0))
    out_rate = float(entry.get("output_per_mtok", 0.0))
    return (input_tokens / 1_000_000.0) * in_rate + (
        output_tokens / 1_000_000.0
    ) * out_rate


def estimate_image_cost(*, provider: str, model: str, images: int = 1) -> float:
    key = _normalize_key(provider, model)
    overrides = _load_overrides()
    override = overrides.get(key)
    if override and "per_image_usd" in override:
        return float(override["per_image_usd"]) * images
    rate = _IMAGE_PRICES.get(key, 0.0)
    return rate * images


def known_token_prices() -> dict[str, dict[str, float]]:
    """Return the merged token price table (defaults + overrides)."""
    merged: dict[str, dict[str, float]] = {}
    for k, v in _TOKEN_PRICES.items():
        merged[k] = dict(v)
    for k, v in _load_overrides().items():
        if not isinstance(v, dict):
            continue
        merged.setdefault(k, {}).update(
            {kk: float(vv) for kk, vv in v.items() if isinstance(vv, (int, float))}
        )
    return merged


def known_image_prices() -> dict[str, float]:
    merged = dict(_IMAGE_PRICES)
    for k, v in _load_overrides().items():
        if isinstance(v, dict) and "per_image_usd" in v:
            try:
                merged[k] = float(v["per_image_usd"])
            except TypeError, ValueError:
                continue
    return merged


def pricing_snapshot() -> dict[str, Any]:
    """Snapshot for the diagnostics endpoint."""
    return {
        "token_prices": known_token_prices(),
        "image_prices": known_image_prices(),
        "overrides_active": bool(_load_overrides()),
    }
