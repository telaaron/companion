"""Multi-turn agent loop that executes local tools server-side.

PR3 (this module) ships the non-streaming variant: each upstream turn is
fully drained into a :class:`CollectedMessage`, tool_use blocks are dispatched
to local executors, and the loop continues until the model returns
``stop_reason=end_turn`` (or ``max_turns`` is exhausted).

PR4 will add the SSE-interleaving variant that yields synthetic blocks to the
client between upstream turns.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger

from api.agent.text_tool_parser import extract_text_form_tool_uses
from api.models.anthropic import MessagesRequest, Tool
from core.tools import registry as tool_registry
from core.tools.registry import AgentContext
from core.tools.result import ToolResult
from core.tools.sse_collector import CollectedMessage, collect
from core.tools.workspace import Workspace


class StreamingProvider(Protocol):
    """Minimal provider surface the agent loop depends on."""

    def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]: ...


DEFAULT_MAX_TURNS = 10


@dataclass(slots=True)
class ToolInvocation:
    """Record of a single local tool call performed during the loop."""

    tool_use_id: str
    name: str
    input: dict[str, Any]
    result: ToolResult


@dataclass(slots=True)
class AgentRun:
    """Outcome of an :func:`run_agent_to_completion` execution."""

    final_message: CollectedMessage
    turns: int
    invocations: list[ToolInvocation] = field(default_factory=list)
    halted_reason: str = "end_turn"

    def to_anthropic_response(self) -> dict[str, Any]:
        msg = self.final_message
        return {
            "id": msg.message_id or "msg_agent_loop",
            "type": "message",
            "role": "assistant",
            "model": msg.model or "",
            "content": list(msg.content_blocks),
            "stop_reason": msg.stop_reason or self.halted_reason,
            "stop_sequence": msg.stop_sequence,
            "usage": dict(msg.usage),
        }


async def run_agent_to_completion(
    request: MessagesRequest,
    provider: StreamingProvider,
    workspace: Workspace,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    request_id: str | None = None,
    bash_denylist: tuple[str, ...] = (),
    bash_extra_env_allowlist: tuple[str, ...] = (),
    tool_call_limit_per_minute: int = 0,
    global_limiter: Any = None,
) -> AgentRun:
    """Run the agent loop until the model finishes or the turn cap is hit.

    The caller's ``request`` object is mutated only via a deep-copied
    ``messages`` list (Pydantic models stay untouched).
    """
    from api.agent.rate_limit import ToolCallRateLimiter

    working_messages = _clone_messages(request.messages)
    invocations: list[ToolInvocation] = []
    final: CollectedMessage = CollectedMessage()
    halted_reason = "end_turn"
    limiter = ToolCallRateLimiter(limit_per_minute=tool_call_limit_per_minute)
    agent_ctx = AgentContext(
        bash_denylist=bash_denylist,
        bash_extra_env_allowlist=bash_extra_env_allowlist,
    )
    identity_prompt = build_identity_prompt(request.model, str(workspace.root))

    for turn in range(1, max_turns + 1):
        upstream_request = _build_request_for_turn(
            request, working_messages, identity_prompt=identity_prompt
        )
        stream = provider.stream_response(upstream_request, request_id=request_id)
        final = await collect(stream)

        if final.error:
            halted_reason = "upstream_error"
            return AgentRun(
                final_message=final,
                turns=turn,
                invocations=invocations,
                halted_reason=halted_reason,
            )

        # Promote XML-style tool calls embedded in plain text into native form.
        _promote_text_form_tool_calls_collected(final)

        if final.stop_reason != "tool_use":
            halted_reason = final.stop_reason or "end_turn"
            return AgentRun(
                final_message=final,
                turns=turn,
                invocations=invocations,
                halted_reason=halted_reason,
            )

        tool_uses = final.tool_uses()
        if not tool_uses:
            logger.warning(
                "Agent loop: stop_reason=tool_use but no tool_use blocks present"
            )
            halted_reason = "no_tool_use_blocks"
            return AgentRun(
                final_message=final,
                turns=turn,
                invocations=invocations,
                halted_reason=halted_reason,
            )

        working_messages.append(final.to_assistant_message())

        tool_result_blocks: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            invocation = await dispatch_tool_audited(
                tool_use,
                workspace,
                agent_ctx,
                request_id=request_id,
                limiter=limiter,
                global_limiter=global_limiter,
            )
            invocations.append(invocation)
            tool_result_blocks.append(
                invocation.result.as_tool_result_block(invocation.tool_use_id)
            )

        working_messages.append({"role": "user", "content": tool_result_blocks})

    halted_reason = "max_turns"
    logger.warning("Agent loop: hit max_turns={} cap", max_turns)
    return AgentRun(
        final_message=final,
        turns=max_turns,
        invocations=invocations,
        halted_reason=halted_reason,
    )


def _promote_text_form_tool_calls_collected(message: CollectedMessage) -> None:
    """Append synthetic ``tool_use`` blocks parsed from text content."""
    if message.stop_reason == "tool_use" and message.tool_uses():
        return
    extracted: list[dict[str, Any]] = []
    for block in message.content_blocks:
        if block.get("type") != "text":
            continue
        text = block.get("text") or ""
        _, parsed = extract_text_form_tool_uses(text)
        extracted.extend(parsed)
    if not extracted:
        return
    message.content_blocks.extend(extracted)
    message.stop_reason = "tool_use"


async def dispatch_tool_audited(
    tool_use: dict[str, Any],
    workspace: Workspace,
    agent_ctx: AgentContext,
    *,
    request_id: str | None,
    limiter: Any,
    global_limiter: Any = None,
) -> ToolInvocation:
    """Run one tool call with rate-limit gate + audit trace emission."""
    from api.agent.audit import record_rate_limited, record_tool_invocation

    name = tool_use.get("name") or ""

    # Per-request limit first, then process-wide limit. Either rejects → audit.
    for active, scope in ((limiter, "per_request"), (global_limiter, "global")):
        if active is None:
            continue
        if not active.acquire():
            limit = getattr(active, "limit_per_minute", 0)
            record_rate_limited(
                request_id=request_id,
                workspace_root=str(workspace.root),
                tool_name=name,
                limit_per_minute=limit,
            )
            raw = tool_use.get("input")
            safe_input: dict[str, Any] = raw if isinstance(raw, dict) else {}
            return ToolInvocation(
                tool_use_id=tool_use.get("id") or "",
                name=name,
                input=safe_input,
                result=ToolResult(
                    content=(
                        f"Error: tool-call rate limit ({limit}/min, scope={scope}) "
                        "exceeded"
                    ),
                    is_error=True,
                ),
            )

    # Snapshot the file BEFORE Edit/Write so we can emit a unified diff after.
    pre_snapshot: str | None = None
    if name in ("Edit", "Write"):
        raw = tool_use.get("input")
        target = raw.get("file_path") if isinstance(raw, dict) else None
        if isinstance(target, str) and target:
            try:
                from pathlib import Path as _Path

                p = _Path(target)
                if not p.is_absolute():
                    p = workspace.root / p
                p = p.resolve(strict=False)
                if p.is_file():
                    pre_snapshot = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pre_snapshot = None

    started = time.monotonic()
    invocation = await _dispatch_tool(tool_use, workspace, agent_ctx)
    duration_ms = int((time.monotonic() - started) * 1000)
    record_tool_invocation(
        invocation,
        request_id=request_id,
        workspace_root=str(workspace.root),
        duration_ms=duration_ms,
    )
    # Persist file_edits row + unified diff metadata for the diff renderer.
    if name in ("Edit", "Write") and not invocation.result.is_error:
        _record_file_edit_with_diff(invocation, workspace, pre_snapshot, request_id)
    return invocation


def _record_file_edit_with_diff(
    invocation: ToolInvocation,
    workspace: Workspace,
    pre_snapshot: str | None,
    request_id: str | None,
) -> None:
    """Append a file_edits row including a unified diff in metadata.

    Renders ~50 lines max of unified diff so the dashboard can show inline
    review of every Edit/Write. Errors get swallowed — recording is
    best-effort and must not block the agent loop.
    """
    try:
        import difflib

        from api import datastore

        meta = invocation.result.metadata or {}
        target_path = meta.get("path") or invocation.input.get("file_path", "")
        bytes_written = int(meta.get("bytes_written") or 0)
        post_snapshot = ""
        try:
            from pathlib import Path as _Path

            p = _Path(target_path)
            if p.is_file():
                post_snapshot = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            post_snapshot = ""
        diff_lines = list(
            difflib.unified_diff(
                (pre_snapshot or "").splitlines(),
                post_snapshot.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
                n=2,
            )
        )
        # Cap diff to keep DB rows small.
        if len(diff_lines) > 400:
            diff_lines = [*diff_lines[:400], "… (truncated)"]
        op = (
            "edit"
            if invocation.name == "Edit"
            else ("create" if meta.get("created") else "write")
        )
        bytes_delta = bytes_written - len(pre_snapshot or "")
        datastore.record_file_edit(
            op=op,
            path=str(target_path),
            bytes_delta=bytes_delta,
            metadata={
                "tool": invocation.name,
                "request_id": request_id,
                "replacements": meta.get("replacements"),
                "diff": "\n".join(diff_lines),
                "had_prior": pre_snapshot is not None,
            },
        )
    except Exception:
        pass


async def _dispatch_tool(
    tool_use: dict[str, Any],
    workspace: Workspace,
    agent_ctx: AgentContext,
) -> ToolInvocation:
    name = tool_use.get("name") or ""
    tool_use_id = tool_use.get("id") or ""
    raw_input = tool_use.get("input")
    tool_input: dict[str, Any]
    if isinstance(raw_input, dict):
        tool_input = raw_input
    elif isinstance(raw_input, str):
        try:
            parsed = json.loads(raw_input)
            tool_input = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            tool_input = {}
    else:
        tool_input = {}

    spec = tool_registry.get(name)
    if spec is None:
        result = ToolResult(content=f"Error: unknown tool '{name}'", is_error=True)
        return ToolInvocation(tool_use_id, name, tool_input, result)

    extra_kwargs = spec.build_extra_kwargs(agent_ctx)

    try:
        result = await spec.executor(tool_input, workspace, **extra_kwargs)
    except asyncio.CancelledError:
        # Propagate cancellation so the streaming loop terminates cleanly.
        raise
    except Exception as exc:
        logger.exception("Tool {} raised: {}", name, exc)
        result = ToolResult(
            content=f"Error: tool '{name}' raised {type(exc).__name__}: {exc}",
            is_error=True,
        )
    return ToolInvocation(tool_use_id, name, tool_input, result)


_IDENTITY_PROMPT_TEMPLATE = (
    "You are **Companion**, the assistant the user runs through their local "
    "free-claude-code proxy. The underlying model identifier is `{model}`. "
    "Do NOT claim to be Claude, ChatGPT, or any other branded AI — when "
    "asked who you are, say you are Companion powered by `{model}`.\n\n"
    "Your capabilities:\n"
    "* **Sandboxed local tools** (Read, Write, Edit, LS, Glob, Grep, Bash) "
    "scoped to the workspace at `{workspace}`. Use them; never describe "
    "what you would do.\n"
    "* **Self-management tools**: ProjectList / ProjectCreate / ProjectUpdate / "
    "ProjectDelete (manage the user's projects), EnvSet (rotate env vars + "
    "hot-reload), Search (FTS5 across past chat turns + indexed memory).\n"
    "* **Preference tools**: PreferenceSet / PreferenceDelete / PreferenceList. "
    "When the user expresses a long-term preference (e.g. 'always ask before "
    "deleting files', 'always answer in German'), call PreferenceSet to record "
    "it immediately. Always summarise what you recorded in one short sentence. "
    "When the user revokes a preference, call PreferenceDelete.\n"
    "* **Cloud CLIs via Bash** when tokens are configured: `gh` (GitHub), "
    "`vercel` (Vercel), `supabase` (Supabase). Env vars in your shell: "
    "GITHUB_TOKEN, VERCEL_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, "
    "OPENAI_API_KEY, ANTHROPIC_API_KEY.\n"
    "* **Image generation**: trigger an image by prefixing a user request "
    "with `imagine:` OR call the configured IMAGE_GEN_PROVIDER. Common "
    "phrasings that should generate an image: 'create an image of …', "
    "'generate a picture of …', 'mach mir ein bild von …', 'design a "
    "logo for …'.\n"
    "* **Obsidian vaults** (if a plugin is registered under `plugins/`): "
    "`Obsidian<Name>List` / `Read` / `Append` to manage Markdown notes.\n\n"
    "Style: terse, accurate, action-first. Show what you did, not what "
    "you considered doing."
)


def build_identity_prompt(model: str, workspace: str) -> str:
    base = _IDENTITY_PROMPT_TEMPLATE.format(
        model=model or "unknown", workspace=workspace or "(cwd)"
    )
    try:
        from api.agent.extras.preferences import load_prefs_text

        prefs_text = load_prefs_text()
        if prefs_text:
            return base + "\n\n" + prefs_text
    except Exception:
        pass
    return base


def _build_request_for_turn(
    request: MessagesRequest, messages: list[Any], *, identity_prompt: str | None = None
) -> MessagesRequest:
    """Return a copy of ``request`` with refreshed ``messages`` and tools merged."""
    tool_defs = tool_registry.anthropic_tool_definitions()
    existing = list(request.tools or [])
    existing_names = {t.name for t in existing}
    merged: list[Tool] = list(existing)
    for defn in tool_defs:
        if defn["name"] in existing_names:
            continue
        merged.append(Tool(**defn))

    update: dict[str, Any] = {"messages": messages, "tools": merged}
    if identity_prompt:
        existing_system = request.system
        if existing_system is None:
            update["system"] = identity_prompt
        elif isinstance(existing_system, str):
            update["system"] = identity_prompt + "\n\n" + existing_system
        # list-form system left untouched — caller already structured it.
    return request.model_copy(update=update)


def _clone_messages(messages: list[Any]) -> list[Any]:
    """Return a list with a shallow clone of each message preserved as input."""
    cloned: list[Any] = []
    for msg in messages:
        if hasattr(msg, "model_copy"):
            cloned.append(msg.model_copy(deep=True))
        else:
            cloned.append(msg)
    return cloned
