"""Tests for the skill marketplace (api/agent/extras/skills.py + dashboard_routes).

Covers:
- SKILL.md front-matter parsing
- Skill directory loader
- SkillRun tool: success, non-zero exit, timeout, path-traversal block
- GET /v1/skills/local
- GET /v1/skills/catalog (None catalog URL → empty; tmp HTTP server → cached)
- POST /v1/skills/install/{slug} (fixture tarball from tmp HTTP server)
- DELETE /v1/skills/install/{slug}
"""

from __future__ import annotations

import http.server
import io
import json
import shutil
import tarfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

FIXTURE_SKILL_DIR = Path(__file__).parent.parent / "fixtures" / "skills" / "echo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tarball(skill_dir: Path) -> bytes:
    """Create an in-memory .tar.gz of the skill_dir contents."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(skill_dir.rglob("*")):
            arcname = p.relative_to(skill_dir.parent)
            tf.add(str(p), arcname=str(arcname))
    return buf.getvalue()


class _TinyHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves pre-registered paths."""

    from typing import ClassVar

    routes: ClassVar[dict[str, tuple[str, bytes]]] = {}

    def do_GET(self) -> None:
        if self.path in self.routes:
            content_type, body = self.routes[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # silence output during tests


def _start_http_server(
    routes: dict[str, tuple[str, bytes]],
) -> tuple[str, http.server.HTTPServer]:
    """Start a local HTTP server on an ephemeral port; return (base_url, server)."""
    handler = type(
        "_Handler",
        (_TinyHTTPHandler,),
        {"routes": routes},
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}", server


# ---------------------------------------------------------------------------
# Unit: front-matter parsing
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_parses_yaml_block(self) -> None:
        from api.agent.extras.skills import _parse_frontmatter

        text = "---\nname: mypkg\ndescription: does stuff\nentry: run.py\n---\n"
        fm = _parse_frontmatter(text)
        assert fm["name"] == "mypkg"
        assert fm["description"] == "does stuff"
        assert fm["entry"] == "run.py"

    def test_no_frontmatter_fallback(self) -> None:
        from api.agent.extras.skills import _parse_frontmatter

        text = "name: simple\ndescription: bare\n"
        fm = _parse_frontmatter(text)
        assert fm.get("name") == "simple"

    def test_strips_quotes(self) -> None:
        from api.agent.extras.skills import _parse_frontmatter

        text = "---\nname: 'quoted'\ndescription: \"also quoted\"\n---\n"
        fm = _parse_frontmatter(text)
        assert fm["name"] == "quoted"
        assert fm["description"] == "also quoted"


# ---------------------------------------------------------------------------
# Unit: Skill.from_skill_md
# ---------------------------------------------------------------------------


class TestSkillFromMd:
    def test_loads_fixture(self) -> None:
        from api.agent.extras.skills import Skill

        skill = Skill.from_skill_md(FIXTURE_SKILL_DIR / "SKILL.md")
        assert skill is not None
        assert skill.name == "echo"
        assert "echo" in skill.description.lower()
        assert skill.entry == "entry.py"
        assert skill.version == "0.1.0"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from api.agent.extras.skills import Skill

        result = Skill.from_skill_md(tmp_path / "nonexistent" / "SKILL.md")
        assert result is None


# ---------------------------------------------------------------------------
# Unit: safe entry validation
# ---------------------------------------------------------------------------


class TestSafeEntry:
    def test_valid_entry(self, tmp_path: Path) -> None:
        from api.agent.extras.skills import _safe_entry

        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        (skill_dir / "entry.py").write_text("print('hi')")
        result = _safe_entry(skill_dir, "entry.py")
        assert result is not None
        assert result.name == "entry.py"

    def test_traversal_blocked(self, tmp_path: Path) -> None:
        from api.agent.extras.skills import _safe_entry

        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        result = _safe_entry(skill_dir, "../../evil.py")
        assert result is None

    def test_absolute_path_blocked(self, tmp_path: Path) -> None:
        from api.agent.extras.skills import _safe_entry

        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        result = _safe_entry(skill_dir, "/etc/passwd")
        assert result is None


# ---------------------------------------------------------------------------
# Unit: loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_load_skills_finds_fixture(self) -> None:
        from api.agent.extras.skills import load_skills

        skills = load_skills(FIXTURE_SKILL_DIR.parent)
        assert "echo" in skills
        assert skills["echo"].entry == "entry.py"

    def test_load_skills_empty_dir(self, tmp_path: Path) -> None:
        from api.agent.extras.skills import load_skills

        skills = load_skills(tmp_path)
        assert skills == {}

    def test_rescan_updates_cache(self, tmp_path: Path) -> None:
        from api.agent.extras import skills as skills_mod

        # Point to empty dir
        skills_mod._LOADED_SKILLS = {}
        skills_mod.rescan_skills(tmp_path)
        assert len(skills_mod._LOADED_SKILLS) == 0

        # Add a skill and rescan
        sd = tmp_path / "mypkg"
        sd.mkdir()
        (sd / "SKILL.md").write_text(
            "---\nname: mypkg\ndescription: test\nentry: entry.py\n---\n"
        )
        (sd / "entry.py").write_text("print('ok')")
        skills_mod.rescan_skills(tmp_path)
        assert "mypkg" in skills_mod._LOADED_SKILLS


# ---------------------------------------------------------------------------
# Async: SkillRun tool
# ---------------------------------------------------------------------------


class TestSkillRunTool:
    @pytest.fixture(autouse=True)
    def _point_to_fixture(self, tmp_path: Path):
        """Copy the fixture skill into a tmp dir and patch the skills root."""
        skills_root = tmp_path / "skills"
        shutil.copytree(FIXTURE_SKILL_DIR.parent, skills_root)

        from api.agent.extras import skills as skills_mod

        skills_mod.rescan_skills(skills_root)
        yield
        skills_mod._LOADED_SKILLS = {}

    @pytest.mark.asyncio
    async def test_run_echo_skill(self) -> None:
        from api.agent.extras.skills import execute_skill_run

        ws = MagicMock()
        result = await execute_skill_run(
            {"name": "echo", "args": {"text": "hello"}}, ws
        )
        assert not result.is_error
        assert "hello" in result.content

    @pytest.mark.asyncio
    async def test_run_missing_skill(self) -> None:
        from api.agent.extras.skills import execute_skill_run

        ws = MagicMock()
        result = await execute_skill_run({"name": "nonexistent"}, ws)
        assert result.is_error
        assert "not installed" in result.content

    @pytest.mark.asyncio
    async def test_run_missing_name(self) -> None:
        from api.agent.extras.skills import execute_skill_run

        ws = MagicMock()
        result = await execute_skill_run({}, ws)
        assert result.is_error
        assert "'name' is required" in result.content

    @pytest.mark.asyncio
    async def test_run_nonzero_exit(self, tmp_path: Path) -> None:
        from api.agent.extras import skills as skills_mod
        from api.agent.extras.skills import execute_skill_run

        skills_root = tmp_path / "skills2"
        fail_dir = skills_root / "fail"
        fail_dir.mkdir(parents=True)
        (fail_dir / "SKILL.md").write_text(
            "---\nname: fail\ndescription: always fails\nentry: entry.py\n---\n"
        )
        (fail_dir / "entry.py").write_text("import sys; sys.exit(1)")
        skills_mod.rescan_skills(skills_root)

        ws = MagicMock()
        result = await execute_skill_run({"name": "fail"}, ws)
        assert result.is_error
        assert "exited 1" in result.content

    @pytest.mark.asyncio
    async def test_run_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.agent.extras import skills
        from api.agent.extras import skills as skills_mod

        # Patch timeout to 0.1s to make the test fast
        monkeypatch.setattr(skills, "_SKILL_RUN_TIMEOUT_S", 0.1)

        skills_root = tmp_path / "skills3"
        slow_dir = skills_root / "slow"
        slow_dir.mkdir(parents=True)
        (slow_dir / "SKILL.md").write_text(
            "---\nname: slow\ndescription: sleeps forever\nentry: entry.py\n---\n"
        )
        (slow_dir / "entry.py").write_text("import time; time.sleep(9999)")
        skills_mod.rescan_skills(skills_root)

        ws = MagicMock()
        result = await skills.execute_skill_run({"name": "slow"}, ws)
        assert result.is_error
        assert "timed out" in result.content

    @pytest.mark.asyncio
    async def test_path_traversal_in_entry_blocked(self, tmp_path: Path) -> None:
        from api.agent.extras import skills as skills_mod
        from api.agent.extras.skills import execute_skill_run

        skills_root = tmp_path / "skills4"
        evil_dir = skills_root / "evil"
        evil_dir.mkdir(parents=True)
        (evil_dir / "SKILL.md").write_text(
            "---\nname: evil\ndescription: bad actor\nentry: ../../evil.py\n---\n"
        )
        skills_mod.rescan_skills(skills_root)

        ws = MagicMock()
        result = await execute_skill_run({"name": "evil"}, ws)
        assert result.is_error
        assert "escapes" in result.content


# ---------------------------------------------------------------------------
# Async: SkillList tool
# ---------------------------------------------------------------------------


class TestSkillListTool:
    @pytest.mark.asyncio
    async def test_returns_skills(self, tmp_path: Path) -> None:
        from api.agent.extras import skills as skills_mod
        from api.agent.extras.skills import execute_skill_list

        shutil.copytree(FIXTURE_SKILL_DIR.parent, tmp_path / "skills")
        skills_mod.rescan_skills(tmp_path / "skills")

        ws = MagicMock()
        result = await execute_skill_list({}, ws)
        assert not result.is_error
        data = json.loads(result.content)
        names = [s["name"] for s in data["skills"]]
        assert "echo" in names


# ---------------------------------------------------------------------------
# HTTP: /v1/skills/local  /v1/skills/catalog  install/uninstall
# ---------------------------------------------------------------------------


def _make_app() -> Any:
    from api.app import create_app

    return create_app()


@pytest.fixture()
def client(tmp_path: Path):
    """TestClient with a fresh SQLite datastore and skills dir."""
    from api import datastore

    db_file = tmp_path / "test.sqlite3"
    datastore.reset_for_tests(db_file)

    app = _make_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


class TestSkillsLocalRoute:
    def test_returns_empty_when_no_skills(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from api.agent.extras import skills as skills_mod

        skills_mod._LOADED_SKILLS = {}
        with (
            patch(
                "api.agent.extras.skills._skills_root", return_value=tmp_path / "empty"
            ),
            patch(
                "api.dashboard_routes._skills_root",
                return_value=tmp_path / "empty",
            ),
        ):
            resp = client.get("/v1/skills/local")
        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == []

    def test_returns_installed_skills(self, client: TestClient, tmp_path: Path) -> None:
        from api.agent.extras import skills as skills_mod

        skills_root = tmp_path / "skills"
        shutil.copytree(FIXTURE_SKILL_DIR.parent, skills_root)
        skills_mod.rescan_skills(skills_root)

        with patch("api.dashboard_routes._skills_root", return_value=skills_root):
            resp = client.get("/v1/skills/local")

        assert resp.status_code == 200
        data = resp.json()
        names = [s["name"] for s in data["skills"]]
        assert "echo" in names


class TestSkillsCatalogRoute:
    def test_no_catalog_url_returns_empty(self, client: TestClient) -> None:
        """When SKILLS_CATALOG_URL is None, catalog returns empty list."""
        from config.settings import Settings

        mock_settings = MagicMock(spec=Settings)
        mock_settings.skills_catalog_url = None

        with patch("api.dashboard_routes.get_settings", return_value=mock_settings):
            resp = client.get("/v1/skills/catalog")

        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == []
        assert data["source"] is None

    def test_unreachable_url_returns_empty(self, client: TestClient) -> None:
        """When catalog URL is unreachable, catalog returns empty list (no 500)."""
        from config.settings import Settings

        mock_settings = MagicMock(spec=Settings)
        mock_settings.skills_catalog_url = "http://127.0.0.1:1"  # nothing listening

        with (
            patch("api.dashboard_routes.get_settings", return_value=mock_settings),
            patch("api.dashboard_routes._load_cached_catalog", return_value=None),
        ):
            resp = client.get("/v1/skills/catalog")

        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == []
        assert "error" in data

    def test_catalog_from_tmp_server(self, client: TestClient) -> None:
        """Remote catalog served from tmp HTTP server is returned correctly."""
        from config.settings import Settings

        catalog_data = [
            {
                "slug": "echo",
                "name": "echo",
                "description": "Echoes text",
                "download_url": "http://example.com/echo.tar.gz",
            }
        ]
        catalog_bytes = json.dumps(catalog_data).encode()
        routes = {"/index.json": ("application/json", catalog_bytes)}
        base_url, server = _start_http_server(routes)
        try:
            mock_settings = MagicMock(spec=Settings)
            mock_settings.skills_catalog_url = f"{base_url}/index.json"

            with (
                patch("api.dashboard_routes.get_settings", return_value=mock_settings),
                patch("api.dashboard_routes._load_cached_catalog", return_value=None),
                patch("api.dashboard_routes._save_catalog_cache"),
            ):
                resp = client.get("/v1/skills/catalog")
        finally:
            server.shutdown()

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["slug"] == "echo"


class TestSkillsInstallRoute:
    def test_install_from_tmp_server(self, client: TestClient, tmp_path: Path) -> None:
        """POST /v1/skills/install/echo downloads + extracts fixture tarball."""
        from config.settings import Settings

        # Build a tarball of the echo fixture skill
        tarball = _make_tarball(FIXTURE_SKILL_DIR)

        catalog_data = [
            {
                "slug": "echo",
                "name": "echo",
                "description": "Echoes text",
                "download_url": "",  # will be patched below
            }
        ]

        # Set up tmp server
        tarball_path = "/echo.tar.gz"
        catalog_path = "/index.json"
        routes: dict[str, tuple[str, bytes]] = {}

        base_url, server = _start_http_server(routes)
        try:
            catalog_data[0]["download_url"] = f"{base_url}{tarball_path}"
            routes[catalog_path] = (
                "application/json",
                json.dumps(catalog_data).encode(),
            )
            routes[tarball_path] = ("application/octet-stream", tarball)

            mock_settings = MagicMock(spec=Settings)
            mock_settings.skills_catalog_url = f"{base_url}{catalog_path}"
            mock_settings.skills_dir = str(tmp_path / "skills_install")

            skills_root = tmp_path / "skills_install"

            with (
                patch("api.dashboard_routes.get_settings", return_value=mock_settings),
                patch("api.dashboard_routes._skills_root", return_value=skills_root),
            ):
                resp = client.post("/v1/skills/install/echo")
        finally:
            server.shutdown()

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["slug"] == "echo"
        # SKILL.md should exist
        assert (Path(data["path"]) / "SKILL.md").is_file()

    def test_install_invalid_slug(self, client: TestClient) -> None:
        resp = client.post("/v1/skills/install/../evil")
        assert resp.status_code in (400, 404, 422)

    def test_install_no_catalog_url(self, client: TestClient) -> None:
        from config.settings import Settings

        mock_settings = MagicMock(spec=Settings)
        mock_settings.skills_catalog_url = None

        with patch("api.dashboard_routes.get_settings", return_value=mock_settings):
            resp = client.post("/v1/skills/install/echo")

        assert resp.status_code == 400

    def test_uninstall_removes_dir(self, client: TestClient, tmp_path: Path) -> None:
        from api.agent.extras import skills as skills_mod

        skills_root = tmp_path / "skills_uninstall"
        echo_dir = skills_root / "echo"
        shutil.copytree(FIXTURE_SKILL_DIR, echo_dir)
        skills_mod.rescan_skills(skills_root)

        with patch("api.dashboard_routes._skills_root", return_value=skills_root):
            resp = client.delete("/v1/skills/install/echo")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert not echo_dir.exists()

    def test_uninstall_missing_skill(self, client: TestClient, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills_empty"
        skills_root.mkdir()
        with patch("api.dashboard_routes._skills_root", return_value=skills_root):
            resp = client.delete("/v1/skills/install/nothere")
        assert resp.status_code == 404
