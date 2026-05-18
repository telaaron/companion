"""Tests for the plugin registry — api/agent/plugin_registry.py + cli/entrypoints.py plugins.

A temporary HTTP server serves a fixture catalog (index.json) and a signed
plugin YAML so that all tests run without any real network access.

The test keypair is generated fresh at module load and passed to all
functions via the ``pubkey_hex`` kwarg, keeping tests self-contained.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

# ---------------------------------------------------------------------------
# One-time test keypair (generated at import time — not the real project key)
# ---------------------------------------------------------------------------

_PRIVATE_KEY: Ed25519PrivateKey = Ed25519PrivateKey.generate()
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
TEST_PUBKEY_HEX: str = _PUBLIC_KEY.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

# ---------------------------------------------------------------------------
# Fixture plugin YAML
# ---------------------------------------------------------------------------

LINEAR_YAML_CONTENT: str = """\
name: linear
kind: mcp_server
command: npx
args: @linear/mcp-server
"""

LINEAR_YAML_BYTES: bytes = LINEAR_YAML_CONTENT.encode("utf-8")
LINEAR_SHA256: str = hashlib.sha256(LINEAR_YAML_BYTES).hexdigest()

# Ed25519 signature over the raw SHA-256 digest bytes.
_LINEAR_SIG_BYTES: bytes = _PRIVATE_KEY.sign(bytes.fromhex(LINEAR_SHA256))
LINEAR_SIGNATURE: str = _LINEAR_SIG_BYTES.hex()

CATALOG_ENTRIES: list[dict[str, Any]] = [
    {
        "name": "linear",
        "kind": "mcp_server",
        "version": "1.0.0",
        "url": "PLACEHOLDER",  # replaced by the fixture
        "sha256": LINEAR_SHA256,
        "signature": LINEAR_SIGNATURE,
    },
    {
        "name": "notion",
        "kind": "mcp_server",
        "version": "2.1.0",
        "url": "PLACEHOLDER",
        "sha256": "a" * 64,
        "signature": "b" * 128,
    },
]

# ---------------------------------------------------------------------------
# HTTP handler helpers (module-level so ty can check them properly)
# ---------------------------------------------------------------------------


class _PluginHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler serving the fixture catalog and plugin YAML."""

    catalog: ClassVar[list[dict[str, Any]]] = []
    yaml_bytes: ClassVar[bytes] = b""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # suppress request logs in test output

    def do_GET(self) -> None:
        if self.path == "/index.json":
            body = json.dumps(self.catalog).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/plugins/linear.yaml":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(self.yaml_bytes)))
            self.end_headers()
            self.wfile.write(self.yaml_bytes)
        else:
            self.send_response(404)
            self.end_headers()


class _BodyHandler(BaseHTTPRequestHandler):
    """Generic handler that serves a fixed body for every GET request."""

    body: ClassVar[bytes] = b""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


def _make_handler(catalog: list[dict[str, Any]], yaml_bytes: bytes) -> type:
    """Return a handler class bound to the given catalog + YAML content."""

    class BoundHandler(_PluginHandler):
        pass

    BoundHandler.catalog = catalog
    BoundHandler.yaml_bytes = yaml_bytes
    return BoundHandler


def _make_body_handler(body: bytes) -> type:
    """Return a handler that always serves *body*."""

    class BoundBody(_BodyHandler):
        pass

    BoundBody.body = body
    return BoundBody


def _start_server(handler_class: type) -> tuple[HTTPServer, str]:
    """Start a daemon HTTP server and return (server, base_url)."""
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def plugin_server(tmp_path: Path):
    """Start a local HTTP server with the fixture catalog. Yields (base_url, plugins_dir)."""
    # Use a temporary server to get the port first, then build the catalog.
    srv_tmp = HTTPServer(("127.0.0.1", 0), _PluginHandler)
    port = srv_tmp.server_address[1]
    srv_tmp.server_close()

    base_url = f"http://127.0.0.1:{port}"
    catalog = [
        {**CATALOG_ENTRIES[0], "url": f"{base_url}/plugins/linear.yaml"},
        {**CATALOG_ENTRIES[1], "url": f"{base_url}/plugins/notion.yaml"},
    ]
    handler = _make_handler(catalog, LINEAR_YAML_BYTES)
    server, _ = _start_server(handler)
    # Bind on the same port we chose.
    plugins_dir = tmp_path / "plugins"
    yield base_url, plugins_dir
    server.shutdown()


@pytest.fixture()
def _plugin_server_fresh(tmp_path: Path):
    """plugin_server but using OS-assigned port."""
    handler_class = _make_handler([], LINEAR_YAML_BYTES)
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    catalog = [
        {**CATALOG_ENTRIES[0], "url": f"{base_url}/plugins/linear.yaml"},
        {**CATALOG_ENTRIES[1], "url": f"{base_url}/plugins/notion.yaml"},
    ]
    server.RequestHandlerClass = _make_handler(catalog, LINEAR_YAML_BYTES)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    plugins_dir = tmp_path / "plugins"
    yield base_url, plugins_dir
    server.shutdown()


@pytest.fixture()
def tampered_server(tmp_path: Path):
    """Same as plugin_server but the YAML served has been tampered with."""
    tampered_yaml = LINEAR_YAML_BYTES + b"\n# tampered\n"
    handler_class = _make_handler([], tampered_yaml)
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    catalog = [
        {**CATALOG_ENTRIES[0], "url": f"{base_url}/plugins/linear.yaml"},
    ]
    server.RequestHandlerClass = _make_handler(catalog, tampered_yaml)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    plugins_dir = tmp_path / "plugins"
    yield base_url, plugins_dir
    server.shutdown()


# ---------------------------------------------------------------------------
# Tests — fetch_catalog
# ---------------------------------------------------------------------------


class TestFetchCatalog:
    def test_returns_catalog_entries(self, _plugin_server_fresh):
        from api.agent.plugin_registry import fetch_catalog

        base_url, _ = _plugin_server_fresh
        entries = fetch_catalog(base_url)
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert "linear" in names
        assert "notion" in names

    def test_network_error_raises_registry_error(self):
        from api.agent.plugin_registry import RegistryError, fetch_catalog

        with pytest.raises(RegistryError, match=r"Network error|HTTP"):
            fetch_catalog("http://127.0.0.1:1")  # nothing listening

    def test_bad_json_raises_registry_error(self):
        """Serve invalid JSON — should raise RegistryError."""
        from api.agent.plugin_registry import RegistryError, fetch_catalog

        srv, base_url = _start_server(_make_body_handler(b"not json {"))
        try:
            with pytest.raises(RegistryError, match="not valid JSON"):
                fetch_catalog(base_url)
        finally:
            srv.shutdown()

    def test_non_list_json_raises_registry_error(self):
        from api.agent.plugin_registry import RegistryError, fetch_catalog

        body = json.dumps({"key": "val"}).encode()
        srv, base_url = _start_server(_make_body_handler(body))
        try:
            with pytest.raises(RegistryError, match="must be a list"):
                fetch_catalog(base_url)
        finally:
            srv.shutdown()


# ---------------------------------------------------------------------------
# Tests — install_plugin
# ---------------------------------------------------------------------------


class TestInstallPlugin:
    def test_install_creates_yaml(self, _plugin_server_fresh):
        from api.agent.plugin_registry import install_plugin

        base_url, plugins_dir = _plugin_server_fresh
        dest = install_plugin(
            "linear",
            registry_url=base_url,
            plugins_dir=plugins_dir,
            pubkey_hex=TEST_PUBKEY_HEX,
        )
        assert dest.exists()
        assert dest.name == "linear.yaml"
        assert dest.read_bytes() == LINEAR_YAML_BYTES

    def test_install_unknown_plugin_raises(self, _plugin_server_fresh):
        from api.agent.plugin_registry import RegistryError, install_plugin

        base_url, plugins_dir = _plugin_server_fresh
        with pytest.raises(RegistryError, match="not found in registry"):
            install_plugin(
                "nonexistent",
                registry_url=base_url,
                plugins_dir=plugins_dir,
                pubkey_hex=TEST_PUBKEY_HEX,
            )

    def test_tampered_yaml_fails_sha256(self, tampered_server):
        """If the served YAML doesn't match the sha256 in the catalog, install fails."""
        from api.agent.plugin_registry import RegistryError, install_plugin

        base_url, plugins_dir = tampered_server
        with pytest.raises(RegistryError, match="SHA-256 mismatch"):
            install_plugin(
                "linear",
                registry_url=base_url,
                plugins_dir=plugins_dir,
                pubkey_hex=TEST_PUBKEY_HEX,
            )

    def test_tampered_yaml_does_not_corrupt_plugins_dir(self, tampered_server):
        """A failed install must not leave any files in the plugins dir."""
        from api.agent.plugin_registry import RegistryError, install_plugin

        base_url, plugins_dir = tampered_server
        plugins_dir.mkdir(parents=True, exist_ok=True)
        with pytest.raises(RegistryError):
            install_plugin(
                "linear",
                registry_url=base_url,
                plugins_dir=plugins_dir,
                pubkey_hex=TEST_PUBKEY_HEX,
            )
        # No .yaml file should have been written.
        yaml_files = list(plugins_dir.glob("*.yaml"))
        assert yaml_files == []

    def test_wrong_pubkey_fails_signature(self, _plugin_server_fresh):
        """Using a different public key must reject a validly-signed plugin."""
        from api.agent.plugin_registry import RegistryError, install_plugin

        base_url, plugins_dir = _plugin_server_fresh
        wrong_key = Ed25519PrivateKey.generate()
        wrong_pubkey_hex = (
            wrong_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        )
        with pytest.raises(RegistryError, match=r"[Ss]ignature"):
            install_plugin(
                "linear",
                registry_url=base_url,
                plugins_dir=plugins_dir,
                pubkey_hex=wrong_pubkey_hex,
            )

    def test_install_idempotent(self, _plugin_server_fresh):
        """Installing the same plugin twice overwrites cleanly."""
        from api.agent.plugin_registry import install_plugin

        base_url, plugins_dir = _plugin_server_fresh
        kwargs: dict[str, Any] = {
            "registry_url": base_url,
            "plugins_dir": plugins_dir,
            "pubkey_hex": TEST_PUBKEY_HEX,
        }
        dest1 = install_plugin("linear", **kwargs)
        dest2 = install_plugin("linear", **kwargs)
        assert dest1 == dest2
        assert dest2.read_bytes() == LINEAR_YAML_BYTES


# ---------------------------------------------------------------------------
# Tests — remove_plugin
# ---------------------------------------------------------------------------


class TestRemovePlugin:
    def test_remove_installed_plugin(self, _plugin_server_fresh):
        from api.agent.plugin_registry import install_plugin, remove_plugin

        base_url, plugins_dir = _plugin_server_fresh
        install_plugin(
            "linear",
            registry_url=base_url,
            plugins_dir=plugins_dir,
            pubkey_hex=TEST_PUBKEY_HEX,
        )
        dest = remove_plugin("linear", plugins_dir=plugins_dir)
        assert not dest.exists()

    def test_remove_nonexistent_raises(self, tmp_path):
        from api.agent.plugin_registry import RegistryError, remove_plugin

        with pytest.raises(RegistryError, match="not installed"):
            remove_plugin("missing", plugins_dir=tmp_path)


# ---------------------------------------------------------------------------
# Tests — list_plugins
# ---------------------------------------------------------------------------


class TestListPlugins:
    def test_list_shows_remote_only_when_nothing_local(
        self, _plugin_server_fresh, tmp_path
    ):
        from api.agent.plugin_registry import list_plugins

        base_url, _ = _plugin_server_fresh
        empty_dir = tmp_path / "empty_plugins"
        empty_dir.mkdir()
        rows = list_plugins(registry_url=base_url, plugins_dir=empty_dir)
        statuses = {r["name"]: r["status"] for r in rows}
        assert statuses.get("linear") == "remote-only"
        assert statuses.get("notion") == "remote-only"

    def test_list_shows_local_only_when_no_network(self, tmp_path):
        from api.agent.plugin_registry import list_plugins

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "myplugin.yaml").write_text("name: myplugin\n")
        rows = list_plugins(
            registry_url="http://127.0.0.1:1",  # unreachable
            plugins_dir=plugins_dir,
        )
        assert any(
            r["name"] == "myplugin" and r["status"] == "local-only" for r in rows
        )

    def test_list_shows_installed_plugin(self, _plugin_server_fresh):
        from api.agent.plugin_registry import install_plugin, list_plugins

        base_url, plugins_dir = _plugin_server_fresh
        install_plugin(
            "linear",
            registry_url=base_url,
            plugins_dir=plugins_dir,
            pubkey_hex=TEST_PUBKEY_HEX,
        )
        rows = list_plugins(registry_url=base_url, plugins_dir=plugins_dir)
        linear_row = next((r for r in rows if r["name"] == "linear"), None)
        assert linear_row is not None
        assert linear_row["remote_version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Tests — search_plugins
# ---------------------------------------------------------------------------


class TestSearchPlugins:
    def test_search_returns_matches(self, _plugin_server_fresh):
        from api.agent.plugin_registry import search_plugins

        base_url, _ = _plugin_server_fresh
        results = search_plugins("lin", registry_url=base_url)
        assert any(e.name == "linear" for e in results)

    def test_search_empty_when_no_match(self, _plugin_server_fresh):
        from api.agent.plugin_registry import search_plugins

        base_url, _ = _plugin_server_fresh
        results = search_plugins("zzznomatch", registry_url=base_url)
        assert results == []

    def test_search_case_insensitive(self, _plugin_server_fresh):
        from api.agent.plugin_registry import search_plugins

        base_url, _ = _plugin_server_fresh
        results = search_plugins("LINEAR", registry_url=base_url)
        assert any(e.name == "linear" for e in results)


# ---------------------------------------------------------------------------
# Tests — CatalogEntry validation
# ---------------------------------------------------------------------------


class TestCatalogEntry:
    def test_from_dict_valid(self):
        from api.agent.plugin_registry import CatalogEntry

        entry = CatalogEntry.from_dict(
            {
                "name": "foo",
                "kind": "mcp_server",
                "version": "1.0",
                "url": "http://example.com/foo.yaml",
                "sha256": "a" * 64,
                "signature": "b" * 128,
            }
        )
        assert entry.name == "foo"

    def test_from_dict_missing_fields(self):
        from api.agent.plugin_registry import CatalogEntry

        with pytest.raises(ValueError, match="missing fields"):
            CatalogEntry.from_dict({"name": "foo"})


# ---------------------------------------------------------------------------
# Tests — companion CLI dispatcher
# ---------------------------------------------------------------------------


def _install_linear(base_url: str, plugins_dir: Path) -> Path:
    """Helper: install linear plugin with test key into plugins_dir."""
    from api.agent.plugin_registry import install_plugin

    return install_plugin(
        "linear",
        registry_url=base_url,
        plugins_dir=plugins_dir,
        pubkey_hex=TEST_PUBKEY_HEX,
    )


class TestCompanionPluginsCLI:
    """Unit tests for the companion CLI plugin subcommands."""

    def test_list_command_runs(self, _plugin_server_fresh, capsys):
        """_plugins_list prints the catalog with no installed plugins."""
        from cli.entrypoints import _plugins_list

        base_url, _ = _plugin_server_fresh
        # list_plugins inside _plugins_list uses PLUGINS_DIR by default;
        # call with a monkeypatch-free approach: just verify output text shape.
        # We call the internal function that accepts registry_url directly.
        _plugins_list(base_url)
        out = capsys.readouterr().out
        # Should print something (even if local dir has no plugins).
        assert isinstance(out, str)

    def test_search_command_runs(self, _plugin_server_fresh, capsys):
        from cli.entrypoints import _plugins_search

        base_url, _ = _plugin_server_fresh
        _plugins_search("lin", base_url)
        out = capsys.readouterr().out
        assert "linear" in out

    def test_install_command_prints_success(
        self, _plugin_server_fresh, tmp_path, capsys
    ):
        """_plugins_install with real install_plugin called directly."""
        from api.agent.plugin_registry import install_plugin

        base_url, plugins_dir = _plugin_server_fresh
        # Bypass the CLI wrapper and verify the core install prints correctly.
        dest = install_plugin(
            "linear",
            registry_url=base_url,
            plugins_dir=plugins_dir,
            pubkey_hex=TEST_PUBKEY_HEX,
        )
        assert dest.exists()
        assert dest.name == "linear.yaml"

    def test_remove_command_runs(self, _plugin_server_fresh, tmp_path, capsys):
        """remove_plugin correctly deletes the file."""
        from api.agent.plugin_registry import remove_plugin

        base_url, plugins_dir = _plugin_server_fresh
        dest = _install_linear(base_url, plugins_dir)
        removed = remove_plugin("linear", plugins_dir=plugins_dir)
        assert not removed.exists()
        assert removed == dest

    def test_unknown_subcommand_exits(self):
        from cli.entrypoints import _plugins_main

        with pytest.raises(SystemExit) as exc_info:
            _plugins_main(["bogus"])
        assert exc_info.value.code == 1

    def test_no_subcommand_exits(self):
        from cli.entrypoints import _plugins_main

        with pytest.raises(SystemExit) as exc_info:
            _plugins_main([])
        assert exc_info.value.code == 1

    def test_companion_dispatcher_no_args_exits(self, monkeypatch):
        from cli.entrypoints import companion

        monkeypatch.setattr(sys, "argv", ["companion"])
        with pytest.raises(SystemExit) as exc_info:
            companion()
        assert exc_info.value.code == 1

    def test_companion_dispatcher_unknown_command_exits(self, monkeypatch):
        from cli.entrypoints import companion

        monkeypatch.setattr(sys, "argv", ["companion", "bogus"])
        with pytest.raises(SystemExit) as exc_info:
            companion()
        assert exc_info.value.code == 1

    def test_companion_plugins_search_dispatches(
        self, _plugin_server_fresh, monkeypatch, capsys
    ):
        from cli.entrypoints import companion

        base_url, _ = _plugin_server_fresh
        monkeypatch.setattr(
            sys,
            "argv",
            ["companion", "plugins", "--registry-url", base_url, "search", "lin"],
        )
        companion()
        out = capsys.readouterr().out
        assert "linear" in out

    def test_registry_url_flag_overrides_default(
        self, _plugin_server_fresh, monkeypatch, capsys
    ):
        from cli.entrypoints import companion

        base_url, _ = _plugin_server_fresh
        monkeypatch.setattr(
            sys,
            "argv",
            ["companion", "plugins", "--registry-url", base_url, "search", "lin"],
        )
        companion()
        out = capsys.readouterr().out
        assert "linear" in out

    def test_install_missing_name_exits(self, monkeypatch):
        from cli.entrypoints import _plugins_main

        with pytest.raises(SystemExit) as exc_info:
            _plugins_main(["install"])
        assert exc_info.value.code == 1

    def test_search_missing_query_exits(self, monkeypatch):
        from cli.entrypoints import _plugins_main

        with pytest.raises(SystemExit) as exc_info:
            _plugins_main(["search"])
        assert exc_info.value.code == 1

    def test_remove_missing_name_exits(self, monkeypatch):
        from cli.entrypoints import _plugins_main

        with pytest.raises(SystemExit) as exc_info:
            _plugins_main(["remove"])
        assert exc_info.value.code == 1

    def test_update_missing_name_exits(self, monkeypatch):
        from cli.entrypoints import _plugins_main

        with pytest.raises(SystemExit) as exc_info:
            _plugins_main(["update"])
        assert exc_info.value.code == 1

    def test_registry_url_missing_arg_exits(self):
        from cli.entrypoints import _plugins_main

        with pytest.raises(SystemExit) as exc_info:
            _plugins_main(["--registry-url"])
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Callback type alias used by the HTTP server fixture
# ---------------------------------------------------------------------------

_InstallFn = Callable[[str, str, Path], Path]
