# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ``companion-bin`` frozen binary.

Build from the repo root with::

    uv run pyinstaller packaging/companion-bin.spec \\
        --distpath packaging/dist \\
        --workpath packaging/build \\
        --noconfirm

The resulting binary lives at ``packaging/dist/companion-bin`` (macOS /
Linux) or ``packaging/dist/companion-bin.exe`` (Windows). Tauri picks it
up via ``bundle.resources`` in ``tauri/src-tauri/tauri.conf.json``.

Hidden imports cover the dynamic plumbing in FastAPI / uvicorn / pydantic
that the static analyzer cannot see. ``collect_submodules`` over the
in-repo packages catches provider plugins that ``cli.entrypoints``
imports lazily.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

REPO = Path(SPECPATH).resolve().parent

# Bundled runtime assets — anything ``cli.entrypoints`` or the FastAPI app
# touches at runtime must be present in the unpacked bundle.
datas: list[tuple[str, str]] = [
    (str(REPO / "api" / "ui_static"), "api/ui_static"),
    (str(REPO / "plugins"), "plugins"),
    (str(REPO / "routines"), "routines"),
    (str(REPO / ".env.example"), "."),
]

# Pull in tiktoken's bundled BPE encoder files — required for token counts.
datas += collect_data_files("tiktoken")
datas += collect_data_files("tiktoken_ext")

# Dynamic imports that PyInstaller's static analyzer will miss.
hiddenimports: list[str] = []
for pkg in (
    "uvicorn",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.websockets",
    "uvicorn.loops",
    "uvicorn.lifespan",
    "fastapi",
    "pydantic",
    "anyio",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "api",
    "core",
    "config",
    "providers",
    "messaging",
    "plugins",
    "cli",
):
    hiddenimports.extend(collect_submodules(pkg))


a = Analysis(
    [str(REPO / "packaging" / "companion-bin-entry.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Test-only / packaging tooling that has no business in the runtime
        # bundle. Excluding them shaves real megabytes.
        "pytest",
        "pyinstaller",
        "_pytest",
        "PIL",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="companion-bin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,  # keep stdout/stderr visible during early dev
    target_arch=None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)
