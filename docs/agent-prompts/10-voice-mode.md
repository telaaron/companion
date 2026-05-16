# 2.5 — Voice mode

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Hold spacebar in the chat input → speak → Whisper transcribes → AI
replies → reply is spoken back via macOS `say` or ElevenLabs.

## Files

- `api/dashboard_routes.py`:
  - `POST /v1/transcribe` — accepts a multipart audio blob (webm/opus),
    transcribes via `whisper.cpp` subprocess **or** routed to an upstream
    transcription endpoint. Returns `{text, duration_ms}`.
  - `POST /v1/tts` — `{text, voice}` → audio bytes (mp3 or wav). macOS
    `say` shell command OR ElevenLabs API depending on `Settings.tts_provider`.
- `api/ui_static/app.js` — `MediaRecorder` on hold-spacebar in the
  chat textarea. Visual mic indicator (red dot + pulsing ring). On
  release → POST to `/v1/transcribe` → insert text into the composer.
  Optional auto-submit if `voice_auto_send` setting is on.
- `config/settings.py` — `whisper_binary` (path), `whisper_model`
  (default `small`), `tts_provider` (`mac_say` | `elevenlabs` | `none`),
  `elevenlabs_api_key`, `elevenlabs_voice_id`.
- `tests/api/test_voice.py` (new) — mock `whisper.cpp` subprocess to
  return a fixed JSON, assert endpoint returns its `text`.

## Implementation plan

1. Decide transport: webm/opus from the browser, decode to wav with
   `ffmpeg` (or pipe the raw bytes to `whisper.cpp` which accepts opus
   via libsndfile). Document the `ffmpeg` requirement.
2. UI: track spacebar `keydown`/`keyup` on the textarea. Hold > 200 ms →
   begin recording. Release → stop + send. Show transcription progress
   inline.
3. TTS: stream the assistant reply through a chunker. As each sentence
   completes, POST it to `/v1/tts` and queue playback via `<audio>`.
   Skip code blocks (don't read XML to the user).
4. Tests: subprocess mocked via `monkeypatch`; route handler returns the
   fixed transcript.

## Acceptance

- Hold space → red mic indicator → speak *"summarize my last 3 chats
  about X"* → release → text appears in composer within 2 s → reply
  streams + plays back over speakers.
- Code blocks in the reply are not spoken.

## Risks

- Microphone access requires HTTPS or localhost; document that remote
  users need Cloudflare Access (Phase 5.2) for HTTPS.
- ElevenLabs is per-character billed — show a usage indicator.

## Verify

```bash
uv run pytest tests/api/test_voice.py -v
uv run pytest -v --tb=short
```
