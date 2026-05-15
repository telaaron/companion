"""Workspace sandbox for local tool execution.

All file-system tool inputs are routed through :class:`Workspace` so that paths
outside the configured root are rejected. Symlinks are resolved before the
containment check to prevent escape via symbolic links.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Hardening caps on user-supplied paths.
_MAX_PATH_BYTES = 4096
_MAX_COMPONENT_BYTES = 255  # Linux NAME_MAX; matches macOS APFS too.


class WorkspaceViolation(Exception):
    """Raised when a tool attempts to access a path outside the workspace."""


@dataclass(frozen=True, slots=True)
class Workspace:
    """Sandbox root for filesystem tool calls.

    ``root`` is the canonical (resolved) directory inside which all tool I/O
    must stay. When ``allow_outside_root`` is True the resolver returns paths
    unchanged — intended only for tests and trusted contexts.
    """

    root: Path
    allow_outside_root: bool = False

    @classmethod
    def create(cls, root: str | Path, *, allow_outside_root: bool = False) -> Workspace:
        resolved = Path(root).expanduser().resolve(strict=False)
        return cls(root=resolved, allow_outside_root=allow_outside_root)

    def resolve(self, user_path: str | Path) -> Path:
        """Resolve ``user_path`` and enforce containment within ``root``.

        Relative paths are interpreted against the workspace root. Symlinks are
        followed via ``Path.resolve`` before the containment check.
        """
        if user_path in (None, ""):
            raise WorkspaceViolation("Empty path is not allowed")

        text = str(user_path)
        if "\x00" in text:
            raise WorkspaceViolation("Path must not contain NUL bytes")
        if len(text.encode("utf-8")) > _MAX_PATH_BYTES:
            raise WorkspaceViolation(
                f"Path too long ({len(text)} chars, max {_MAX_PATH_BYTES} bytes)"
            )
        for component in Path(text).parts:
            if len(component.encode("utf-8")) > _MAX_COMPONENT_BYTES:
                raise WorkspaceViolation(
                    f"Path component too long (>{_MAX_COMPONENT_BYTES} bytes): "
                    f"{component[:40]}…"
                )

        candidate = Path(user_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate

        resolved = candidate.resolve(strict=False)

        if self.allow_outside_root:
            return resolved

        if resolved == self.root:
            return resolved
        if self.root not in resolved.parents:
            raise WorkspaceViolation(
                f"Path {resolved} is outside workspace {self.root}"
            )
        return resolved
