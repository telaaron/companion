"""Plugin registry — fetch, verify, and install signed plugins from a remote catalog.

Catalog format (JSON served at ``<registry_url>/index.json``):

.. code-block:: json

    [
      {
        "name": "linear",
        "kind": "mcp_server",
        "version": "1.0.0",
        "url": "https://example.com/plugins/linear.yaml",
        "sha256": "<hex digest of YAML content>",
        "signature": "<hex-encoded Ed25519 signature of sha256 bytes>"
      }
    ]

The ``signature`` field is an Ed25519 detached signature over the raw SHA-256
digest bytes (32 bytes) of the plugin YAML, signed by the project-controlled
key whose public half is stored in :mod:`cli.keys`.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cli.keys import REGISTRY_PUBKEY_HEX

# Default registry base URL — overridable via the ``registry_url`` kwarg.
DEFAULT_REGISTRY_URL: str = (
    "https://raw.githubusercontent.com/telaaron/companion-plugins/main"
)

PLUGINS_DIR: Path = Path(__file__).resolve().parents[2] / "plugins"


@dataclass
class CatalogEntry:
    """One entry from the remote plugin index."""

    name: str
    kind: str
    version: str
    url: str
    sha256: str
    signature: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CatalogEntry:
        missing = [
            f
            for f in ("name", "kind", "version", "url", "sha256", "signature")
            if f not in data
        ]
        if missing:
            raise ValueError(f"Catalog entry missing fields: {missing}")
        return cls(
            name=data["name"],
            kind=data["kind"],
            version=data["version"],
            url=data["url"],
            sha256=data["sha256"],
            signature=data["signature"],
        )


class RegistryError(RuntimeError):
    """Raised when a registry operation fails (network, verification, etc.)."""


def _fetch_bytes(url: str, timeout: int = 15) -> bytes:
    """Download *url* and return raw bytes, raising :exc:`RegistryError` on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RegistryError(f"HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise RegistryError(f"Network error fetching {url}: {exc.reason}") from exc
    except OSError as exc:
        raise RegistryError(f"I/O error fetching {url}: {exc}") from exc


def fetch_catalog(registry_url: str = DEFAULT_REGISTRY_URL) -> list[CatalogEntry]:
    """Download and parse the remote catalog JSON."""
    index_url = registry_url.rstrip("/") + "/index.json"
    raw = _fetch_bytes(index_url)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RegistryError(f"Catalog is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RegistryError("Catalog JSON root must be a list")
    entries: list[CatalogEntry] = []
    for item in data:
        try:
            entries.append(CatalogEntry.from_dict(item))
        except (ValueError, TypeError) as exc:
            raise RegistryError(f"Bad catalog entry: {exc}") from exc
    return entries


def _verify_plugin(
    yaml_bytes: bytes,
    entry: CatalogEntry,
    pubkey_hex: str = REGISTRY_PUBKEY_HEX,
) -> None:
    """Verify sha256 and Ed25519 signature; raise :exc:`RegistryError` on any mismatch."""
    actual_sha256 = hashlib.sha256(yaml_bytes).hexdigest()
    if actual_sha256 != entry.sha256:
        raise RegistryError(
            f"SHA-256 mismatch for plugin '{entry.name}': "
            f"expected {entry.sha256}, got {actual_sha256}"
        )

    try:
        sig_bytes = bytes.fromhex(entry.signature)
        pub_bytes = bytes.fromhex(pubkey_hex)
    except ValueError as exc:
        raise RegistryError(f"Invalid hex in signature or pubkey: {exc}") from exc

    pubkey = Ed25519PublicKey.from_public_bytes(pub_bytes)
    # The message signed is the raw 32-byte SHA-256 digest (not the hex string).
    digest_bytes = bytes.fromhex(actual_sha256)
    try:
        pubkey.verify(sig_bytes, digest_bytes)
    except InvalidSignature as exc:
        raise RegistryError(
            f"Ed25519 signature verification failed for plugin '{entry.name}'"
        ) from exc


def install_plugin(
    name: str,
    *,
    registry_url: str = DEFAULT_REGISTRY_URL,
    plugins_dir: Path | None = None,
    pubkey_hex: str = REGISTRY_PUBKEY_HEX,
) -> Path:
    """Download, verify, and install a plugin by name.

    Returns the path to the installed YAML file.
    Raises :exc:`RegistryError` on any failure (network, sha256, signature).
    The plugins directory is never touched if verification fails.
    """
    target_dir = plugins_dir if plugins_dir is not None else PLUGINS_DIR

    catalog = fetch_catalog(registry_url)
    matches = [e for e in catalog if e.name == name]
    if not matches:
        available = ", ".join(e.name for e in catalog) or "(none)"
        raise RegistryError(
            f"Plugin '{name}' not found in registry. Available: {available}"
        )
    entry = matches[0]

    yaml_bytes = _fetch_bytes(entry.url)
    _verify_plugin(yaml_bytes, entry, pubkey_hex)

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / f"{entry.name}.yaml"

    # Write to a tempfile first, rename on success (atomic on POSIX).
    with tempfile.NamedTemporaryFile(
        dir=target_dir, prefix=f".{entry.name}_", suffix=".yaml.tmp", delete=False
    ) as tmp:
        tmp.write(yaml_bytes)
        tmp_path = Path(tmp.name)

    tmp_path.replace(dest)
    return dest


def remove_plugin(
    name: str,
    *,
    plugins_dir: Path | None = None,
) -> Path:
    """Remove a locally installed plugin YAML.

    Returns the path that was removed. Raises :exc:`RegistryError` if not found.
    """
    target_dir = plugins_dir if plugins_dir is not None else PLUGINS_DIR
    dest = target_dir / f"{name}.yaml"
    if not dest.exists():
        raise RegistryError(
            f"Plugin '{name}' is not installed (file not found: {dest})"
        )
    dest.unlink()
    return dest


def list_plugins(
    *,
    registry_url: str = DEFAULT_REGISTRY_URL,
    plugins_dir: Path | None = None,
) -> list[dict[str, str]]:
    """Return merged local + remote plugin info with version-drift indicators.

    Each dict has keys: ``name``, ``local_version``, ``remote_version``, ``status``.
    ``status`` is one of ``"up-to-date"``, ``"update-available"``, ``"local-only"``,
    ``"remote-only"``.

    Network errors are handled gracefully — if the catalog fetch fails,
    remote info is omitted and local plugins are returned with
    ``status="local-only"``.
    """
    target_dir = plugins_dir if plugins_dir is not None else PLUGINS_DIR

    # Collect locally installed plugins.
    local: dict[str, str] = {}
    if target_dir.is_dir():
        for yaml_path in sorted(target_dir.glob("*.yaml")):
            local[yaml_path.stem] = "unknown"

    # Try to fetch remote catalog.
    remote: dict[str, CatalogEntry] = {}
    try:
        for entry in fetch_catalog(registry_url):
            remote[entry.name] = entry
    except RegistryError:
        pass

    names = sorted(set(local) | set(remote))
    result: list[dict[str, str]] = []
    for name in names:
        local_ver = local.get(name, "")
        remote_entry = remote.get(name)
        remote_ver = remote_entry.version if remote_entry else ""

        if local_ver and remote_ver:
            status = "up-to-date" if local_ver == remote_ver else "update-available"
        elif local_ver:
            status = "local-only"
        else:
            status = "remote-only"

        result.append(
            {
                "name": name,
                "local_version": local_ver,
                "remote_version": remote_ver,
                "status": status,
            }
        )
    return result


def search_plugins(
    query: str,
    *,
    registry_url: str = DEFAULT_REGISTRY_URL,
) -> list[CatalogEntry]:
    """Search the remote catalog for entries whose name contains *query*."""
    catalog = fetch_catalog(registry_url)
    q = query.lower()
    return [e for e in catalog if q in e.name.lower()]
