"""Tests for api/agent/extras/skills.py — skill loader, SkillList, SkillRun.

Covers:
- scan_skills discovers fixture skill directory.
- SkillList returns installed skill metadata.
- SkillRun invokes the echo skill and returns its stdout.
- SkillRun with unknown slug returns is_error=True.
- Path escape validation on entry field.
- 60-second timeout fires and kills the subprocess.
- dashboard routes: GET /v1/skills/local, GET /v1/skills/catalog (no URL).
- POST /v1/skills/install/{slug} extracts a fixture tarball.
- DELETE /v1/skills/local/{slug} removes an installed skill.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixture skill path
# ---------------------------------------------------------------------------

_FIXTURES_SKILLS = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


# ---------------------------------------------------------------------------
# Unit tests — scan_skills
# ---------------------------------------------------------------------------


class TestScanSkills:
    def test_scan_finds_echo_skill(self):
        from api.agent.extras.skills import scan_skills

        skills = scan_skills(_FIXTURES_SKILLS)
        assert any(s.slug == "echo" for s in skills)

    def test_echo_skill_metadata(self):
        from api.agent.extras.skills import scan_skills

        skills = scan_skills(_FIXTURES_SKILLS)
        echo = next(s for s in skills if s.slug == "echo")
        assert echo.name == "echo"
        assert "echo" in echo.description.lower() or echo.description
        assert echo.entry == "skill.py"

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        from api.agent.extras.skills import scan_skills

        skills = scan_skills(tmp_path / "nonexistent")
        assert skills == []

    def test_dir_without_skill_md_is_skipped(self, tmp_path: Path):
        from api.agent.extras.skills import scan_skills

        (tmp_path / "noskill").mkdir()
        (tmp_path / "noskill" / "script.py").write_text("pass")
        skills = scan_skills(tmp_path)
        assert skills == []

    def test_skill_dir_with_skill_md_is_found(self, tmp_path: Path):
        from api.agent.extras.skills import scan_skills

        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "name: myskill\ndescription: A test skill\nentry: run.py\n"
        )
        skills = scan_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].slug == "myskill"
        assert skills[0].entry == "run.py"


# ---------------------------------------------------------------------------
# Unit tests — _resolve_entry
# ---------------------------------------------------------------------------


class TestResolveEntry:
    def test_valid_entry_resolves(self, tmp_path: Path):
        from api.agent.extras.skills import Skill, _resolve_entry

        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text("pass")
        skill = Skill(
            name="myskill",
            slug="myskill",
            description="",
            entry="skill.py",
            args_schema={},
            path=skill_dir,
        )
        resolved = _resolve_entry(skill)
        assert resolved == (skill_dir / "skill.py").resolve()

    def test_traversal_entry_raises(self, tmp_path: Path):
        from api.agent.extras.skills import Skill, _resolve_entry

        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        skill = Skill(
            name="myskill",
            slug="myskill",
            description="",
            entry="../../../etc/passwd",
            args_schema={},
            path=skill_dir,
        )
        with pytest.raises(ValueError, match="outside skill dir"):
            _resolve_entry(skill)


# ---------------------------------------------------------------------------
# Async tests — SkillList and SkillRun
# ---------------------------------------------------------------------------


class TestSkillListExecutor:
    @pytest.mark.asyncio
    async def test_skill_list_returns_installed(self):
        from api.agent.extras.skills import skill_list_execute
        from core.tools.workspace import Workspace

        ws = Workspace.create(str(Path.cwd()), allow_outside_root=True)
        with patch("api.agent.extras.skills._SKILLS_ROOT", _FIXTURES_SKILLS):
            result = await skill_list_execute({}, ws)
        assert not result.is_error
        assert isinstance(result.content, str)
        data = json.loads(result.content)
        assert "installed_skills" in data
        slugs = [s["slug"] for s in data["installed_skills"]]
        assert "echo" in slugs

    @pytest.mark.asyncio
    async def test_skill_list_empty_when_no_skills(self, tmp_path: Path):
        from api.agent.extras.skills import skill_list_execute
        from core.tools.workspace import Workspace

        ws = Workspace.create(str(tmp_path), allow_outside_root=True)
        with patch("api.agent.extras.skills._SKILLS_ROOT", tmp_path / "empty"):
            result = await skill_list_execute({}, ws)
        assert not result.is_error
        assert isinstance(result.content, str)
        data = json.loads(result.content)
        assert data["count"] == 0


class TestSkillRunExecutor:
    @pytest.mark.asyncio
    async def test_run_echo_skill(self):
        from api.agent.extras.skills import skill_run_execute
        from core.tools.workspace import Workspace

        ws = Workspace.create(str(Path.cwd()), allow_outside_root=True)
        with patch("api.agent.extras.skills._SKILLS_ROOT", _FIXTURES_SKILLS):
            result = await skill_run_execute(
                {"name": "echo", "args": {"text": "hi"}}, ws
            )
        assert not result.is_error, result.content
        assert "hi" in result.content

    @pytest.mark.asyncio
    async def test_run_unknown_skill_is_error(self):
        from api.agent.extras.skills import skill_run_execute
        from core.tools.workspace import Workspace

        ws = Workspace.create(str(Path.cwd()), allow_outside_root=True)
        with patch("api.agent.extras.skills._SKILLS_ROOT", _FIXTURES_SKILLS):
            result = await skill_run_execute({"name": "nonexistent_skill_xyz"}, ws)
        assert result.is_error
        assert isinstance(result.content, str)
        assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_run_missing_name_is_error(self):
        from api.agent.extras.skills import skill_run_execute
        from core.tools.workspace import Workspace

        ws = Workspace.create(str(Path.cwd()), allow_outside_root=True)
        result = await skill_run_execute({}, ws)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_run_nonzero_exit_is_error(self, tmp_path: Path):
        from api.agent.extras.skills import skill_run_execute
        from core.tools.workspace import Workspace

        # Create a skill that exits with code 1
        skill_dir = tmp_path / "failing"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "name: failing\ndescription: Always fails\nentry: skill.py\n"
        )
        (skill_dir / "skill.py").write_text("import sys\nsys.exit(1)\n")
        ws = Workspace.create(str(tmp_path), allow_outside_root=True)
        with patch("api.agent.extras.skills._SKILLS_ROOT", tmp_path):
            result = await skill_run_execute({"name": "failing"}, ws)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_run_timeout_kills_process(self, tmp_path: Path):
        from api.agent.extras.skills import skill_run_execute
        from core.tools.workspace import Workspace

        # Create a skill that sleeps forever
        skill_dir = tmp_path / "sleeper"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "name: sleeper\ndescription: Sleeps forever\nentry: skill.py\n"
        )
        (skill_dir / "skill.py").write_text("import time\ntime.sleep(9999)\n")
        ws = Workspace.create(str(tmp_path), allow_outside_root=True)
        # Patch the timeout to 0.1s so the test is fast
        with (
            patch("api.agent.extras.skills._SKILLS_ROOT", tmp_path),
            patch("api.agent.extras.skills._SKILL_TIMEOUT_S", 0.1),
        ):
            result = await skill_run_execute({"name": "sleeper"}, ws)
        assert result.is_error
        assert isinstance(result.content, str)
        assert "timed out" in result.content.lower()


# ---------------------------------------------------------------------------
# Dashboard route tests
# ---------------------------------------------------------------------------


def _make_app():
    from api.app import create_app

    return create_app()


@pytest.fixture(scope="module")
def client():
    app = _make_app()
    with (
        patch("api.dependencies.resolve_provider", return_value=MagicMock()),
        patch(
            "providers.registry.ProviderRegistry.validate_configured_models",
            new_callable=AsyncMock,
        ),
        patch("providers.registry.ProviderRegistry.start_model_list_refresh"),
        TestClient(app) as tc,
    ):
        yield tc


class TestSkillsLocalRoute:
    def test_local_returns_list(self, client: TestClient, tmp_path: Path):
        # Isolate the ~/.claude scan so the test is deterministic.
        with (
            patch("api.dashboard_routes._REPO_SKILLS_ROOT", _FIXTURES_SKILLS),
            patch("api.dashboard_routes._SKILL_DIRS", ()),
        ):
            resp = client.get("/v1/skills/local")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        slugs = [s["slug"] for s in data["skills"]]
        assert "echo" in slugs

    def test_local_empty_when_no_skills(self, client: TestClient, tmp_path: Path):
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        # Empty both sources: repo-root and the ~/.claude skill dirs.
        with (
            patch("api.dashboard_routes._REPO_SKILLS_ROOT", empty_dir),
            patch("api.dashboard_routes._SKILL_DIRS", ()),
        ):
            resp = client.get("/v1/skills/local")
        assert resp.status_code == 200
        assert resp.json()["skills"] == []

    def test_local_includes_claude_skills(self, client: TestClient, tmp_path: Path):
        # A skill present only under the ~/.claude scan dirs must surface.
        repo_empty = tmp_path / "repo_empty"
        repo_empty.mkdir()
        claude_dir = tmp_path / "claude_skills"
        skill_dir = claude_dir / "my-claude-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: A test skill from claude\n---\n", encoding="utf-8"
        )
        with (
            patch("api.dashboard_routes._REPO_SKILLS_ROOT", repo_empty),
            patch("api.dashboard_routes._SKILL_DIRS", (claude_dir,)),
        ):
            resp = client.get("/v1/skills/local")
        assert resp.status_code == 200
        skills = resp.json()["skills"]
        names = [s["name"] for s in skills]
        assert "my-claude-skill" in names
        claude_skill = next(s for s in skills if s["name"] == "my-claude-skill")
        assert claude_skill["source"] == "claude"
        assert claude_skill["installed"] is True


class TestSkillsCatalogRoute:
    def test_catalog_no_url_returns_empty(self, client: TestClient):
        with patch(
            "api.dashboard_routes.get_settings",
            return_value=MagicMock(skills_catalog_url=None),
        ):
            resp = client.get("/v1/skills/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == []
        assert data.get("source") is None

    def test_catalog_with_url_fetches_and_returns(self, client: TestClient):
        fake_catalog = {
            "skills": [
                {
                    "slug": "pdf",
                    "name": "PDF",
                    "description": "PDF skill",
                    "tarball_url": "http://example.com/pdf.tar.gz",
                },
            ]
        }

        # Test _fetch_catalog directly (unit test bypass for the FastAPI Depends)
        # and test the full route with a dependency override.
        from api.app import create_app
        from api.dependencies import get_settings as _orig_get_settings

        test_app = create_app()

        mock_settings_obj = MagicMock()
        mock_settings_obj.skills_catalog_url = "http://test.example.com/index.json"
        mock_settings_obj.anthropic_auth_token = ""

        def _settings_override():
            return mock_settings_obj

        async def _fake_fetch(url: str) -> dict:
            return fake_catalog

        test_app.dependency_overrides[_orig_get_settings] = _settings_override

        with (
            patch(
                "providers.registry.ProviderRegistry.validate_configured_models",
                new_callable=AsyncMock,
            ),
            patch("providers.registry.ProviderRegistry.start_model_list_refresh"),
            patch("api.dashboard_routes._fetch_catalog", new=_fake_fetch),
            TestClient(test_app) as tc,
        ):
            resp = tc.get("/v1/skills/catalog")
        test_app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["slug"] == "pdf"

    def test_catalog_unreachable_returns_empty(self, client: TestClient):
        from api.app import create_app
        from api.dependencies import get_settings as _orig_get_settings

        test_app = create_app()

        mock_settings_obj = MagicMock()
        mock_settings_obj.skills_catalog_url = "http://unreachable.test/index.json"
        mock_settings_obj.anthropic_auth_token = ""

        def _settings_override():
            return mock_settings_obj

        async def _failing_fetch(url: str) -> dict:
            return {"skills": []}

        test_app.dependency_overrides[_orig_get_settings] = _settings_override

        with (
            patch(
                "providers.registry.ProviderRegistry.validate_configured_models",
                new_callable=AsyncMock,
            ),
            patch("providers.registry.ProviderRegistry.start_model_list_refresh"),
            patch("api.dashboard_routes._fetch_catalog", new=_failing_fetch),
            TestClient(test_app) as tc,
        ):
            resp = tc.get("/v1/skills/catalog")
        test_app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["skills"] == []


# ---------------------------------------------------------------------------
# Install tests (using a real tmp tarball fixture)
# ---------------------------------------------------------------------------


def _make_skill_tarball(slug: str, skill_md_content: str, script_content: str) -> bytes:
    """Create an in-memory .tar.gz with a skill directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        # Add SKILL.md
        md_bytes = skill_md_content.encode()
        md_info = tarfile.TarInfo(name=f"{slug}/SKILL.md")
        md_info.size = len(md_bytes)
        tf.addfile(md_info, io.BytesIO(md_bytes))
        # Add skill.py
        py_bytes = script_content.encode()
        py_info = tarfile.TarInfo(name=f"{slug}/skill.py")
        py_info.size = len(py_bytes)
        tf.addfile(py_info, io.BytesIO(py_bytes))
    return buf.getvalue()


class TestInstallRoute:
    def test_install_from_direct_url(self, client: TestClient, tmp_path: Path):
        slug = "testskill"
        tarball = _make_skill_tarball(
            slug,
            "name: testskill\ndescription: Test\nentry: skill.py\n",
            "print('ok')\n",
        )
        dest_root = tmp_path / "skills"
        dest_root.mkdir()

        async def _mock_get(url: str) -> httpx.Response:
            return httpx.Response(200, content=tarball)

        with (
            patch("api.dashboard_routes._REPO_SKILLS_ROOT", dest_root),
            patch(
                "api.dashboard_routes.get_settings",
                return_value=MagicMock(skills_catalog_url=None),
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = tarball
            mock_client.get = AsyncMock(return_value=mock_response)

            resp = client.post(
                f"/v1/skills/install/{slug}",
                json={"url": "http://test.local/testskill.tar.gz"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["slug"] == slug

    def test_install_invalid_slug(self, client: TestClient):
        # Slug with spaces/special chars is rejected by regex before download
        resp = client.post(
            "/v1/skills/install/invalid%20slug%21",
            json={"url": "http://x.com/a.tar.gz"},
        )
        assert resp.status_code == 400

    def test_install_no_url_no_catalog(self, client: TestClient):
        with patch(
            "api.dashboard_routes.get_settings",
            return_value=MagicMock(skills_catalog_url=None),
        ):
            resp = client.post("/v1/skills/install/someskill", json={})
        assert resp.status_code == 400


class TestUninstallRoute:
    def test_uninstall_existing(self, client: TestClient, tmp_path: Path):
        dest_root = tmp_path / "skills"
        dest_root.mkdir()
        skill_dir = dest_root / "myskill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("name: myskill\n")

        with patch("api.dashboard_routes._REPO_SKILLS_ROOT", dest_root):
            resp = client.delete("/v1/skills/local/myskill")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not skill_dir.exists()

    def test_uninstall_not_installed(self, client: TestClient, tmp_path: Path):
        dest_root = tmp_path / "skills2"
        dest_root.mkdir()
        with patch("api.dashboard_routes._REPO_SKILLS_ROOT", dest_root):
            resp = client.delete("/v1/skills/local/doesnotexist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tar path validation
# ---------------------------------------------------------------------------


class TestValidateTarPath:
    def test_valid_slug_path(self):
        from api.dashboard_routes import _validate_tar_path

        assert _validate_tar_path("myskill/SKILL.md", "myskill") is True
        assert _validate_tar_path("myskill/sub/script.py", "myskill") is True
        assert _validate_tar_path("myskill", "myskill") is True

    def test_traversal_rejected(self):
        from api.dashboard_routes import _validate_tar_path

        assert _validate_tar_path("../etc/passwd", "myskill") is False
        assert _validate_tar_path("myskill/../../../etc/passwd", "myskill") is False
        assert _validate_tar_path("other/file.txt", "myskill") is False

    def test_absolute_path_rejected(self):
        from api.dashboard_routes import _validate_tar_path

        # Absolute paths strip the leading slash, so /etc/passwd → etc/passwd
        # which doesn't match the prefix "myskill/" → rejected
        assert _validate_tar_path("/etc/passwd", "myskill") is False


# ---------------------------------------------------------------------------
# Import + create endpoints
# ---------------------------------------------------------------------------


class TestSkillImportCreate:
    def test_create_skill(self, client: TestClient, tmp_path: Path):
        root = tmp_path / "repo_skills"
        root.mkdir()
        with patch("api.dashboard_routes._REPO_SKILLS_ROOT", root):
            resp = client.post(
                "/v1/skills/create",
                json={
                    "name": "My Test Skill",
                    "description": "does a thing",
                    "instructions": "use when testing",
                },
            )
        assert resp.status_code == 200
        slug = resp.json()["slug"]
        assert slug == "my-test-skill"
        md = (root / slug / "SKILL.md").read_text()
        assert "name: My Test Skill" in md
        assert "does a thing" in md

    def test_create_with_entry_code(self, client: TestClient, tmp_path: Path):
        root = tmp_path / "repo_skills"
        root.mkdir()
        with patch("api.dashboard_routes._REPO_SKILLS_ROOT", root):
            resp = client.post(
                "/v1/skills/create",
                json={"name": "Coded", "entry_code": "print('hi')"},
            )
        assert resp.status_code == 200
        slug = resp.json()["slug"]
        assert (root / slug / "skill.py").read_text().strip() == "print('hi')"
        assert "entry: skill.py" in (root / slug / "SKILL.md").read_text()

    def test_create_duplicate_rejected(self, client: TestClient, tmp_path: Path):
        root = tmp_path / "repo_skills"
        (root / "dup").mkdir(parents=True)
        with patch("api.dashboard_routes._REPO_SKILLS_ROOT", root):
            resp = client.post("/v1/skills/create", json={"name": "dup"})
        assert resp.status_code == 409

    def test_upload_zip(self, client: TestClient, tmp_path: Path):
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mytool/SKILL.md", "name: My Tool\ndescription: d\n")
            zf.writestr("mytool/skill.py", "print('ok')\n")
        buf.seek(0)
        root = tmp_path / "repo_skills"
        root.mkdir()
        with patch("api.dashboard_routes._REPO_SKILLS_ROOT", root):
            resp = client.post(
                "/v1/skills/upload",
                files={"file": ("mytool.zip", buf.getvalue(), "application/zip")},
            )
        assert resp.status_code == 200
        slug = resp.json()["slug"]
        assert (root / slug / "SKILL.md").is_file()
        assert (root / slug / "skill.py").is_file()

    def test_import_claude_skill(self, client: TestClient, tmp_path: Path):
        claude_dir = tmp_path / "claude"
        skill = claude_dir / "cool-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("name: Cool\ndescription: c\n")
        repo = tmp_path / "repo_skills"
        repo.mkdir()
        with (
            patch("api.dashboard_routes._SKILL_DIRS", (claude_dir,)),
            patch("api.dashboard_routes._REPO_SKILLS_ROOT", repo),
        ):
            resp = client.post("/v1/skills/import-claude/cool-skill")
        assert resp.status_code == 200
        assert (repo / "cool-skill" / "SKILL.md").is_file()

    def test_import_claude_not_found(self, client: TestClient, tmp_path: Path):
        with patch("api.dashboard_routes._SKILL_DIRS", (tmp_path / "nope",)):
            resp = client.post("/v1/skills/import-claude/ghost")
        assert resp.status_code == 404


class TestSkillCreateTool:
    @pytest.mark.asyncio
    async def test_skill_create_executor(self, tmp_path: Path):
        from api.agent.extras import skills as sk
        from core.tools.workspace import Workspace

        root = tmp_path / "skills"
        root.mkdir()
        with patch.object(sk, "_SKILLS_ROOT", root):
            res = await sk.skill_create_execute(
                {
                    "name": "Agent Made",
                    "description": "by the agent",
                    "instructions": "do stuff",
                    "entry_code": "print('x')",
                },
                Workspace.create(tmp_path),
            )
        assert not res.is_error
        assert (root / "agent-made" / "SKILL.md").is_file()
        assert (root / "agent-made" / "skill.py").is_file()
