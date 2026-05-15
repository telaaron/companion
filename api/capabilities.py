"""Capability scanner for the admin UI.

Inspects the running :class:`~config.settings.Settings` and returns a flat
list of :class:`Capability` records grouped into three buckets:

* ``active``   — feature configured and ready (green)
* ``inactive`` — credentials present but not enabled, or partial setup (amber)
* ``suggested`` — not configured at all but worth adding (blue)

The admin UI renders these as cards with a CTA that deep-links to the
relevant config field. Used by ``/admin/api/capabilities``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from config.settings import Settings

CapabilityStatus = Literal["active", "inactive", "suggested"]


@dataclass(frozen=True, slots=True)
class Capability:
    """A single user-visible feature card."""

    id: str
    title: str
    status: CapabilityStatus
    summary: str
    cta_label: str
    cta_field: str | None = None  # admin-form field key the CTA scrolls to


def scan(settings: Settings) -> list[Capability]:
    return [
        *_provider_capabilities(settings),
        *_agent_capabilities(settings),
        *_integration_capabilities(settings),
    ]


def _provider_capabilities(settings: Settings) -> list[Capability]:
    out: list[Capability] = []
    providers = (
        ("DeepSeek", settings.deepseek_api_key, "DEEPSEEK_API_KEY"),
        ("OpenRouter", settings.open_router_api_key, "OPENROUTER_API_KEY"),
        ("NVIDIA NIM", settings.nvidia_nim_api_key, "NVIDIA_NIM_API_KEY"),
        ("Kimi", settings.kimi_api_key, "KIMI_API_KEY"),
        ("Z.ai", settings.zai_api_key, "ZAI_API_KEY"),
        ("OpenCode Zen", settings.opencode_api_key, "OPENCODE_API_KEY"),
        ("Wafer", settings.wafer_api_key, "WAFER_API_KEY"),
    )
    any_active = False
    for label, key, field in providers:
        if key:
            any_active = True
            out.append(
                Capability(
                    id=f"provider_{field.lower()}",
                    title=f"{label} connected",
                    status="active",
                    summary=f"API key set ({_mask(key)}). Models routed via this provider.",
                    cta_label="Manage",
                    cta_field=field,
                )
            )
    if not any_active:
        out.append(
            Capability(
                id="provider_none",
                title="Connect a chat provider",
                status="suggested",
                summary=(
                    "No provider API keys configured yet. Add at least one "
                    "(DeepSeek + OpenRouter cover the broadest model set)."
                ),
                cta_label="Add API key",
                cta_field="DEEPSEEK_API_KEY",
            )
        )
    return out


def _agent_capabilities(settings: Settings) -> list[Capability]:
    out: list[Capability] = []
    if settings.agent_mode_enabled:
        workspace = settings.agent_default_workspace or "(proxy CWD)"
        workspace_ok = (
            Path(settings.agent_default_workspace).expanduser().is_dir()
            if settings.agent_default_workspace
            else True
        )
        if workspace_ok:
            out.append(
                Capability(
                    id="agent_mode",
                    title="Agent mode active",
                    status="active",
                    summary=(
                        f"Local Read/Write/Edit/Bash/Glob/Grep/LS tools enabled. "
                        f"Workspace: {workspace}. Max turns: {settings.agent_max_turns}."
                    ),
                    cta_label="Tune",
                    cta_field="AGENT_MAX_TURNS",
                )
            )
        else:
            out.append(
                Capability(
                    id="agent_workspace_missing",
                    title="Agent workspace missing",
                    status="inactive",
                    summary=(
                        f"AGENT_DEFAULT_WORKSPACE points to {workspace} which "
                        "does not exist. Tools will fall back to the proxy's "
                        "current working directory."
                    ),
                    cta_label="Fix path",
                    cta_field="AGENT_DEFAULT_WORKSPACE",
                )
            )
    else:
        out.append(
            Capability(
                id="agent_mode_off",
                title="Enable agent mode",
                status="suggested",
                summary=(
                    "Let the proxy execute tools (Read/Write/Bash/...) inside "
                    "a sandboxed workspace. Required for file editing and "
                    "shell-based answers."
                ),
                cta_label="Enable",
                cta_field="AGENT_MODE_ENABLED",
            )
        )

    if settings.agent_mode_enabled:
        if not settings.agent_bash_denylist:
            out.append(
                Capability(
                    id="agent_bash_denylist_empty",
                    title="Tighten Bash denylist",
                    status="suggested",
                    summary=(
                        "AGENT_BASH_DENYLIST is empty. Add destructive verbs "
                        "(rm,sudo,curl|sh) to harden against prompt-injected commands."
                    ),
                    cta_label="Configure",
                    cta_field="AGENT_BASH_DENYLIST",
                )
            )
        if settings.agent_global_tool_call_limit_per_minute == 0:
            out.append(
                Capability(
                    id="agent_global_rate",
                    title="Set process-wide tool-call cap",
                    status="suggested",
                    summary=(
                        "No global tool-call rate limit. Set "
                        "AGENT_GLOBAL_TOOL_CALL_LIMIT_PER_MIN to defend against "
                        "runaway concurrent requests."
                    ),
                    cta_label="Configure",
                    cta_field="AGENT_GLOBAL_TOOL_CALL_LIMIT_PER_MIN",
                )
            )
    return out


def _integration_capabilities(settings: Settings) -> list[Capability]:
    out: list[Capability] = []

    if settings.enable_web_server_tools:
        out.append(
            Capability(
                id="web_tools",
                title="Web search + fetch",
                status="active",
                summary=(
                    "Anthropic web_search and web_fetch tools served locally. "
                    f"Allowed schemes: {settings.web_fetch_allowed_schemes}."
                ),
                cta_label="Manage",
                cta_field="ENABLE_WEB_SERVER_TOOLS",
            )
        )
    else:
        out.append(
            Capability(
                id="web_tools_off",
                title="Add web search + fetch",
                status="suggested",
                summary=(
                    "Enable proxy-served web_search / web_fetch so the model "
                    "can browse without provider-side tool support."
                ),
                cta_label="Enable",
                cta_field="ENABLE_WEB_SERVER_TOOLS",
            )
        )

    if not settings.anthropic_auth_token:
        out.append(
            Capability(
                id="auth_token_missing",
                title="Set an API token",
                status="inactive",
                summary=(
                    "ANTHROPIC_AUTH_TOKEN is empty so the proxy accepts any "
                    "request. Required if exposed beyond localhost."
                ),
                cta_label="Set token",
                cta_field="ANTHROPIC_AUTH_TOKEN",
            )
        )

    return out


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def to_payload(items: list[Capability]) -> dict[str, list[dict[str, object]]]:
    """Group capabilities by status for the admin UI."""
    grouped: dict[str, list[dict[str, object]]] = {
        "active": [],
        "inactive": [],
        "suggested": [],
    }
    for cap in items:
        grouped[cap.status].append(asdict(cap))
    return grouped
