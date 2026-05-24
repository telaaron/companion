"""PyInstaller entry shim for ``companion-bin``.

The frozen binary is what the Tauri shell spawns. It must:

1. Repair ``sys.path`` for the frozen layout so package imports work.
2. Fix the working directory so relative file lookups in
   :mod:`cli.entrypoints` (notably the bundled ``.env.example``) resolve
   against the unpacked bundle, not the directory the user clicked from.
3. Hand off to :func:`cli.entrypoints.serve`.

Keep this file tiny on purpose — every line is hot-path for cold-start.
"""

from __future__ import annotations

import os
import sys


def _bootstrap_frozen_env() -> None:
    """Adjust runtime env so the frozen bundle behaves like the source tree.

    When PyInstaller runs in one-file mode it unpacks to ``sys._MEIPASS``.
    ``cli.entrypoints`` resolves ``.env.example`` via
    ``Path(__file__).resolve().parents[1]`` which inside the bundle points
    at ``sys._MEIPASS``. We just need the bundled file to actually live at
    that level, which the spec arranges via ``datas=[(..., '.')]``.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # Chdir into the bundle so any code that does ``open("plugins/...")``
        # without an absolute path still works after the user launches the
        # binary from a random directory (e.g. macOS LaunchServices).
        os.chdir(meipass)


def main() -> None:
    _bootstrap_frozen_env()
    # Import here, not at module top-level, so the path bootstrap above runs
    # before any FastAPI / pydantic / uvicorn import side effects.
    from cli.entrypoints import serve

    serve()


if __name__ == "__main__":
    main()
