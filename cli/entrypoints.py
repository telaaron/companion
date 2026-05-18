"""CLI entry points for the installed package."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import uvicorn

from api.admin_urls import local_proxy_root_url
from api.app import GracefulLifespanApp, create_app
from cli.process_registry import (
    kill_all_best_effort,
    kill_pid_tree_best_effort,
    register_pid,
    unregister_pid,
)
from config.settings import Settings, get_settings

# ---------------------------------------------------------------------------
# companion plugins subcommand
# ---------------------------------------------------------------------------


def _plugins_list(registry_url: str) -> None:
    """Print locally installed plugins with remote version-drift indicators."""
    from api.agent.plugin_registry import RegistryError, list_plugins

    try:
        rows = list_plugins(registry_url=registry_url)
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not rows:
        print("No plugins installed and registry is empty.")
        return

    header = f"{'NAME':<20}  {'LOCAL':<10}  {'REMOTE':<10}  STATUS"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['name']:<20}  {row['local_version'] or '-':<10}  "
            f"{row['remote_version'] or '-':<10}  {row['status']}"
        )


def _plugins_search(query: str, registry_url: str) -> None:
    """Search the remote catalog for plugins matching *query*."""
    from api.agent.plugin_registry import RegistryError, search_plugins

    try:
        results = search_plugins(query, registry_url=registry_url)
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not results:
        print(f"No plugins found matching '{query}'.")
        return

    header = f"{'NAME':<20}  {'VERSION':<10}  KIND"
    print(header)
    print("-" * len(header))
    for entry in results:
        print(f"{entry.name:<20}  {entry.version:<10}  {entry.kind}")


def _plugins_install(name: str, registry_url: str) -> None:
    """Download, verify (sha256 + Ed25519), and install a plugin."""
    from api.agent.plugin_registry import RegistryError, install_plugin

    try:
        dest = install_plugin(name, registry_url=registry_url)
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Plugin '{name}' installed to {dest}.")
    print("Restart the server to load the new plugin.")


def _plugins_remove(name: str) -> None:
    """Remove a locally installed plugin."""
    from api.agent.plugin_registry import RegistryError, remove_plugin

    try:
        dest = remove_plugin(name)
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Plugin '{name}' removed ({dest}).")
    print("Restart the server to apply the change.")


def _plugins_update(name: str, registry_url: str) -> None:
    """Re-install (update) an already-installed plugin."""
    _plugins_install(name, registry_url)


def _plugins_main(argv: list[str]) -> None:
    """Entry point for ``companion plugins <subcommand> …``."""
    from api.agent.plugin_registry import DEFAULT_REGISTRY_URL

    # Minimal argument parsing without adding an argparse dependency.
    registry_url = DEFAULT_REGISTRY_URL
    remaining = argv

    # Allow --registry-url <url> anywhere before the subcommand.
    if "--registry-url" in remaining:
        idx = remaining.index("--registry-url")
        if idx + 1 >= len(remaining):
            print("Error: --registry-url requires an argument.", file=sys.stderr)
            raise SystemExit(1)
        registry_url = remaining[idx + 1]
        remaining = remaining[:idx] + remaining[idx + 2 :]

    if not remaining:
        print(
            "Usage: companion plugins <list|search|install|remove|update> [args]",
            file=sys.stderr,
        )
        raise SystemExit(1)

    subcmd, *rest = remaining

    if subcmd == "list":
        _plugins_list(registry_url)
    elif subcmd == "search":
        if not rest:
            print("Usage: companion plugins search <query>", file=sys.stderr)
            raise SystemExit(1)
        _plugins_search(rest[0], registry_url)
    elif subcmd == "install":
        if not rest:
            print("Usage: companion plugins install <name>", file=sys.stderr)
            raise SystemExit(1)
        _plugins_install(rest[0], registry_url)
    elif subcmd == "remove":
        if not rest:
            print("Usage: companion plugins remove <name>", file=sys.stderr)
            raise SystemExit(1)
        _plugins_remove(rest[0])
    elif subcmd == "update":
        if not rest:
            print("Usage: companion plugins update <name>", file=sys.stderr)
            raise SystemExit(1)
        _plugins_update(rest[0], registry_url)
    else:
        print(
            f"Unknown subcommand '{subcmd}'. "
            "Use: list, search, install, remove, update",
            file=sys.stderr,
        )
        raise SystemExit(1)


def companion() -> None:
    """Top-level ``companion`` CLI dispatcher.

    Currently supports: ``companion plugins <subcommand> …``
    """
    args = sys.argv[1:]
    if not args:
        print("Usage: companion <command> [args]", file=sys.stderr)
        print("Commands: plugins", file=sys.stderr)
        raise SystemExit(1)

    cmd, *rest = args
    if cmd == "plugins":
        _plugins_main(rest)
    else:
        print(
            f"Unknown command '{cmd}'. Available commands: plugins",
            file=sys.stderr,
        )
        raise SystemExit(1)


PROXY_PREFLIGHT_PATH = "/health"
PROXY_PREFLIGHT_TIMEOUT_SECONDS = 1.5
SERVER_GRACEFUL_SHUTDOWN_SECONDS = 5


def _load_env_template() -> str:
    """Load the canonical root env template from package resources or source."""
    import importlib.resources

    packaged = importlib.resources.files("cli").joinpath("env.example")
    if packaged.is_file():
        return packaged.read_text("utf-8")

    source_template = Path(__file__).resolve().parents[1] / ".env.example"
    if source_template.is_file():
        return source_template.read_text(encoding="utf-8")

    raise FileNotFoundError("Could not find bundled or source .env.example template.")


def serve() -> None:
    """Start the FastAPI server (registered as `fcc-server` script)."""
    try:
        try:
            while True:
                settings = get_settings()
                if not _run_supervised_server(settings):
                    return
                get_settings.cache_clear()
        except KeyboardInterrupt:
            return
    finally:
        kill_all_best_effort()


def _run_supervised_server(settings: Settings) -> bool:
    """Run one uvicorn server instance; return whether admin requested restart."""

    restart_requested = False
    server_holder: dict[str, uvicorn.Server] = {}

    def request_restart() -> None:
        nonlocal restart_requested
        restart_requested = True
        if server := server_holder.get("server"):
            server.should_exit = True

    app = create_app(lifespan_enabled=False)
    app.state.admin_restart_callback = request_restart
    asgi_app = GracefulLifespanApp(app)
    config = uvicorn.Config(
        asgi_app,
        host=settings.host,
        port=settings.port,
        log_level="debug",
        timeout_graceful_shutdown=SERVER_GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = uvicorn.Server(config)
    server_holder["server"] = server
    server.run()
    return restart_requested


def init() -> None:
    """Scaffold config at ~/.config/free-claude-code/.env (registered as `fcc-init`)."""
    config_dir = Path.home() / ".config" / "free-claude-code"
    env_file = config_dir / ".env"

    if env_file.exists():
        print(f"Config already exists at {env_file}")
        print("Delete it first if you want to reset to defaults.")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    template = _load_env_template()
    env_file.write_text(template, encoding="utf-8")
    print(f"Config created at {env_file}")
    print("Edit it to set your API keys and model preferences, then run: fcc-server")


def _claude_child_env(
    settings: Settings, base_env: Mapping[str, str]
) -> dict[str, str]:
    """Return a Claude Code environment that targets this proxy."""

    env = {
        key: value
        for key, value in base_env.items()
        if not key.startswith("ANTHROPIC_")
    }
    env["ANTHROPIC_BASE_URL"] = local_proxy_root_url(settings)
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    if token := settings.anthropic_auth_token.strip():
        env["ANTHROPIC_AUTH_TOKEN"] = token
    return env


def _preflight_proxy(proxy_root_url: str) -> str | None:
    """Return an error message when the local proxy health check is unreachable."""

    url = f"{proxy_root_url.rstrip('/')}{PROXY_PREFLIGHT_PATH}"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=PROXY_PREFLIGHT_TIMEOUT_SECONDS) as response:
            status_code = response.getcode()
    except HTTPError as exc:
        return f"returned HTTP {exc.code}"
    except URLError as exc:
        return str(exc.reason)
    except OSError as exc:
        return str(exc)

    if not 200 <= status_code < 300:
        return f"returned HTTP {status_code}"
    return None


def launch_claude(argv: Sequence[str] | None = None) -> None:
    """Launch Claude Code with Free Claude Code proxy environment variables."""

    settings = get_settings()
    proxy_root_url = local_proxy_root_url(settings)
    if error := _preflight_proxy(proxy_root_url):
        print(
            f"Free Claude Code proxy is not reachable at {proxy_root_url}: {error}",
            file=sys.stderr,
        )
        print("Start it in another terminal with: fcc-server", file=sys.stderr)
        raise SystemExit(1)

    args = list(sys.argv[1:] if argv is None else argv)
    claude_command = shutil.which(settings.claude_cli_bin)
    if claude_command is None:
        print(
            f"Could not find Claude Code command: {settings.claude_cli_bin}",
            file=sys.stderr,
        )
        print(
            "Install Claude Code with: npm install -g @anthropic-ai/claude-code",
            file=sys.stderr,
        )
        raise SystemExit(127)

    command = [claude_command, *args]
    env = _claude_child_env(settings, os.environ)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(command, env=env)
        if process.pid:
            register_pid(process.pid)
        return_code = process.wait()
    except FileNotFoundError:
        print(
            f"Could not find Claude Code command: {settings.claude_cli_bin}",
            file=sys.stderr,
        )
        print(
            "Install Claude Code with: npm install -g @anthropic-ai/claude-code",
            file=sys.stderr,
        )
        raise SystemExit(127) from None
    except KeyboardInterrupt:
        if process is not None and process.pid:
            kill_pid_tree_best_effort(process.pid)
            process.wait()
        raise
    finally:
        if process is not None and process.pid:
            unregister_pid(process.pid)

    raise SystemExit(return_code)


def ds(argv: Sequence[str] | None = None) -> None:
    """Backwards-compatible shim — earlier builds shipped a ``ds`` console
    script that started Claude Code through the proxy. The function was
    removed upstream but stale shims in pre-existing venvs still try to
    import it. Keep it as a thin alias to :func:`launch_claude` so the
    user's terminal habit (``ds`` to start chat) keeps working.
    """
    launch_claude(argv)
