#!/usr/bin/env python3
"""Build a Tauri-updater latest.json from the release bundle directory.

The Tauri auto-updater client expects a JSON document of the form::

    {
      "version": "1.1.0",
      "notes": "...",
      "pub_date": "2026-05-27T00:00:00Z",
      "platforms": {
        "darwin-aarch64": {
          "signature": "<base64 minisign signature>",
          "url": "https://github.com/.../Companion.app.tar.gz"
        },
        ...
      }
    }

This script walks ``--bundles-dir`` for ``*.sig`` files, pairs each with
the binary it signs, and writes the platform-specific download URL
(pointing back at the GitHub release this tag is about to create).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

# Map a packaged-bundle filename to the Tauri-updater platform key. The
# rules are intentionally narrow — anything that doesn't match a known
# pattern is logged + skipped so we never emit a half-broken manifest.
_PLATFORM_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.app\.tar\.gz$"), "darwin-aarch64"),
    (re.compile(r"aarch64.*\.dmg$"), "darwin-aarch64"),
    (re.compile(r"x86_64.*\.dmg$"), "darwin-x86_64"),
    (re.compile(r"\.AppImage$"), "linux-x86_64"),
    (re.compile(r"\.tar\.gz$"), "linux-x86_64"),
    (re.compile(r"\.nsis\.zip$"), "windows-x86_64"),
    (re.compile(r"\.msi\.zip$"), "windows-x86_64"),
    (re.compile(r"_x64-setup\.exe$"), "windows-x86_64"),
]


def detect_platform(name: str) -> str | None:
    for pat, plat in _PLATFORM_RULES:
        if pat.search(name):
            return plat
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True, help="Release version without leading v")
    ap.add_argument("--repo", required=True, help="GitHub repo, e.g. telaaron/companion")
    ap.add_argument("--bundles-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--notes", default="See the GitHub release notes.")
    args = ap.parse_args()

    bundles_dir: Path = args.bundles_dir.resolve()
    if not bundles_dir.is_dir():
        print(f"ERROR: bundles dir {bundles_dir} does not exist", file=sys.stderr)
        return 1

    base_url = f"https://github.com/{args.repo}/releases/download/v{args.version}"

    platforms: dict[str, dict[str, str]] = {}
    for sig_path in bundles_dir.rglob("*.sig"):
        # The signed payload is the file with the .sig suffix removed.
        payload = sig_path.with_suffix("")
        if not payload.is_file():
            print(f"WARN: signature {sig_path.name} has no matching payload", file=sys.stderr)
            continue
        plat = detect_platform(payload.name)
        if plat is None:
            print(f"WARN: cannot map {payload.name} to a platform; skipping", file=sys.stderr)
            continue
        try:
            signature = sig_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"WARN: cannot read {sig_path}: {exc}", file=sys.stderr)
            continue
        platforms[plat] = {
            "signature": signature,
            "url": f"{base_url}/{payload.name}",
        }
        print(f"OK: {plat} -> {payload.name}", file=sys.stderr)

    if not platforms:
        print("ERROR: no signed bundles found — auto-updater manifest would be empty", file=sys.stderr)
        return 2

    manifest = {
        "version": args.version,
        "notes": args.notes,
        "pub_date": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"OK: wrote {args.output} with {len(platforms)} platform(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
