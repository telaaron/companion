"""Tests for POST /v1/transcribe and POST /v1/tts.

All external deps (whisper.cpp subprocess, ElevenLabs API, macOS ``say``)
are mocked via monkeypatch — no live network or subprocess calls in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import datastore
from api.app import create_app
from api.dependencies import get_settings
from config.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> Settings:
    """Create a Settings instance with sensible test defaults."""
    s = Settings()
    object.__setattr__(s, "anthropic_auth_token", "")
    for k, v in overrides.items():
        object.__setattr__(s, k, v)
    return s


def _fake_audio() -> bytes:
    """Return a small but non-empty bytes blob to simulate an audio upload."""
    return b"\x00" * 512


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    datastore.reset_for_tests(tmp_path / "test.sqlite")
    app = create_app(lifespan_enabled=False)
    with TestClient(app, client=("127.0.0.1", 50010)) as c:
        yield c, app
    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# POST /v1/transcribe — whisper.cpp path
# ---------------------------------------------------------------------------


def test_transcribe_whisper_cpp_ok(client: tuple[TestClient, Any]) -> None:
    """whisper.cpp subprocess returns JSON transcript → endpoint returns text."""
    c, app = client
    settings = _make_settings(
        voice_note_enabled=True,
        whisper_binary="/usr/local/bin/whisper",
        whisper_model="small",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    fake_json = json.dumps({"transcription": [{"text": "hello world"}]})
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = fake_json
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        r = c.post(
            "/v1/transcribe",
            files={"audio": ("audio.webm", _fake_audio(), "audio/webm")},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "hello world"
    assert isinstance(body["duration_ms"], int)
    assert body["duration_ms"] >= 0


def test_transcribe_whisper_cpp_nonzero_exit(client: tuple[TestClient, Any]) -> None:
    """Non-zero whisper.cpp exit → HTTP 500."""
    c, app = client
    settings = _make_settings(
        voice_note_enabled=True,
        whisper_binary="/usr/local/bin/whisper",
        whisper_model="small",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "model not found"

    with patch("subprocess.run", return_value=mock_result):
        r = c.post(
            "/v1/transcribe",
            files={"audio": ("audio.webm", _fake_audio(), "audio/webm")},
        )

    assert r.status_code == 500


def test_transcribe_voice_disabled(client: tuple[TestClient, Any]) -> None:
    """VOICE_NOTE_ENABLED=false → 503."""
    c, app = client
    settings = _make_settings(voice_note_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings

    r = c.post(
        "/v1/transcribe",
        files={"audio": ("audio.webm", _fake_audio(), "audio/webm")},
    )
    assert r.status_code == 503


def test_transcribe_not_configured(client: tuple[TestClient, Any]) -> None:
    """No binary and not nvidia_nim → 503."""
    c, app = client
    settings = _make_settings(
        voice_note_enabled=True,
        whisper_binary="",
        whisper_device="cpu",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    r = c.post(
        "/v1/transcribe",
        files={"audio": ("audio.webm", _fake_audio(), "audio/webm")},
    )
    assert r.status_code == 503


def test_transcribe_empty_audio(client: tuple[TestClient, Any]) -> None:
    """Empty audio upload → 400."""
    c, app = client
    settings = _make_settings(
        voice_note_enabled=True,
        whisper_binary="/usr/local/bin/whisper",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    r = c.post(
        "/v1/transcribe",
        files={"audio": ("audio.webm", b"", "audio/webm")},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /v1/transcribe — NIM path
# ---------------------------------------------------------------------------


def test_transcribe_nim_ok(
    client: tuple[TestClient, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """nvidia_nim route: mocked httpx returns transcript → endpoint returns text."""

    c, app = client
    settings = _make_settings(
        voice_note_enabled=True,
        whisper_binary="",
        whisper_device="nvidia_nim",
        whisper_model="nvidia/parakeet-ctc-1.1b-asr",
        nvidia_nim_api_key="fake-nim-key",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    async def _mock_nim(audio_bytes: bytes, model: str, api_key: str) -> dict[str, Any]:
        return {"text": "nim transcript", "duration_ms": 42}

    monkeypatch.setattr("api.dashboard_routes._transcribe_via_nim", _mock_nim)

    r = c.post(
        "/v1/transcribe",
        files={"audio": ("audio.webm", _fake_audio(), "audio/webm")},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "nim transcript"


# ---------------------------------------------------------------------------
# POST /v1/tts — mac_say path
# ---------------------------------------------------------------------------


def test_tts_mac_say_ok(
    client: tuple[TestClient, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """mac_say provider: mocked subprocess writes audio file → endpoint returns bytes."""
    c, app = client
    settings = _make_settings(tts_provider="mac_say")
    app.dependency_overrides[get_settings] = lambda: settings

    fake_audio = b"AIFF_FAKE_AUDIO_BYTES"

    def _fake_say_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        # Extract the output path from the command and write fake audio there.
        idx = cmd.index("-o")
        out_path = cmd[idx + 1]
        Path(out_path).write_bytes(fake_audio)
        result = MagicMock()
        result.returncode = 0
        result.stderr = b""
        return result

    with patch("subprocess.run", side_effect=_fake_say_run):
        r = c.post("/v1/tts", json={"text": "Hello world"})

    assert r.status_code == 200
    assert r.content == fake_audio
    assert "audio" in r.headers["content-type"]


def test_tts_mac_say_with_voice(client: tuple[TestClient, Any]) -> None:
    """mac_say with explicit voice: -v flag is passed to say."""
    c, app = client
    settings = _make_settings(tts_provider="mac_say")
    app.dependency_overrides[get_settings] = lambda: settings

    fake_audio = b"AIFF_FAKE"
    captured_cmd: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmd.append(cmd)
        idx = cmd.index("-o")
        out_path = cmd[idx + 1]
        Path(out_path).write_bytes(fake_audio)
        result = MagicMock()
        result.returncode = 0
        result.stderr = b""
        return result

    with patch("subprocess.run", side_effect=_fake_run):
        r = c.post("/v1/tts", json={"text": "Hello", "voice": "Samantha"})

    assert r.status_code == 200
    assert captured_cmd
    cmd = captured_cmd[0]
    assert "-v" in cmd
    assert "Samantha" in cmd


def test_tts_mac_say_failure(client: tuple[TestClient, Any]) -> None:
    """say command exits non-zero → HTTP 500."""
    c, app = client
    settings = _make_settings(tts_provider="mac_say")
    app.dependency_overrides[get_settings] = lambda: settings

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = b"error"

    with patch("subprocess.run", return_value=mock_result):
        r = c.post("/v1/tts", json={"text": "Hello"})

    assert r.status_code == 500


# ---------------------------------------------------------------------------
# POST /v1/tts — ElevenLabs path
# ---------------------------------------------------------------------------


def test_tts_elevenlabs_ok(
    client: tuple[TestClient, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ElevenLabs provider: mocked via _tts_elevenlabs helper → mp3 bytes returned."""
    c, app = client
    settings = _make_settings(
        tts_provider="elevenlabs",
        elevenlabs_api_key="el-fake-key",
        elevenlabs_voice_id="test-voice-id",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    fake_mp3 = b"ID3_FAKE_MP3"

    from fastapi.responses import Response as _Resp

    async def _mock_el(text: str, voice_id: str, api_key: str) -> _Resp:
        return _Resp(content=fake_mp3, media_type="audio/mpeg")

    monkeypatch.setattr("api.dashboard_routes._tts_elevenlabs", _mock_el)

    r = c.post("/v1/tts", json={"text": "Hello ElevenLabs"})

    assert r.status_code == 200
    assert r.content == fake_mp3
    assert "audio" in r.headers["content-type"]


def test_tts_elevenlabs_no_api_key(client: tuple[TestClient, Any]) -> None:
    """ElevenLabs with no api key → 503."""
    c, app = client
    settings = _make_settings(
        tts_provider="elevenlabs",
        elevenlabs_api_key="",
        elevenlabs_voice_id="test-voice-id",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    async def _real_el(text: str, voice_id: str, api_key: str):
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(503, "ELEVENLABS_API_KEY is not configured")

    with patch("api.dashboard_routes._tts_elevenlabs", _real_el):
        r = c.post("/v1/tts", json={"text": "Hello"})

    assert r.status_code == 503


def test_tts_provider_none(client: tuple[TestClient, Any]) -> None:
    """TTS_PROVIDER=none → 503."""
    c, app = client
    settings = _make_settings(tts_provider="none")
    app.dependency_overrides[get_settings] = lambda: settings

    r = c.post("/v1/tts", json={"text": "Hello"})
    assert r.status_code == 503


def test_tts_empty_text(client: tuple[TestClient, Any]) -> None:
    """Empty text → validation error (422) from Pydantic min_length."""
    c, app = client
    settings = _make_settings(tts_provider="mac_say")
    app.dependency_overrides[get_settings] = lambda: settings

    r = c.post("/v1/tts", json={"text": ""})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------


def test_settings_tts_provider_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid tts_provider raises a validation error."""
    monkeypatch.setenv("TTS_PROVIDER", "invalid_provider")
    with pytest.raises(Exception, match="tts_provider"):
        Settings()


def test_settings_voice_defaults() -> None:
    """Voice settings have expected defaults."""
    s = Settings()
    assert s.tts_provider == "mac_say"
    assert s.elevenlabs_voice_id == "21m00Tcm4TlvDq8ikWAM"
    assert s.whisper_binary == ""
    assert s.voice_auto_send is False
