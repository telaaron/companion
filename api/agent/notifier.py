"""Webhook notifier for finished agent jobs.

Posts a message to Slack and/or Discord when a long-running job completes.
Failures are logged and silently swallowed — a bad webhook must never break
the job result.
"""

from __future__ import annotations

import httpx
from loguru import logger

from api import datastore
from config.settings import get_settings

_WEBHOOK_TIMEOUT_S = 5.0


async def notify(job_id: str) -> None:
    """POST completion notifications to configured Slack/Discord webhooks.

    Silently no-ops when no webhook URLs are configured.
    Never raises — all errors are logged and discarded.
    """
    settings = get_settings()
    if not settings.slack_webhook_url and not settings.discord_webhook_url:
        return

    job = datastore.get_agent_job(job_id)
    if not job:
        logger.warning("notifier: job {} not found, skipping webhook", job_id)
        return

    title = _job_title(job)
    model = job.get("model") or "unknown"
    duration_s = _duration_seconds(job)
    last_reply = _last_reply_snippet(job_id)
    session_id = job.get("session_id") or ""
    link = f"{settings.public_base_url.rstrip('/')}/ui/#chat/{session_id}"

    async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT_S) as client:
        if settings.slack_webhook_url:
            await _post_slack(
                client,
                url=settings.slack_webhook_url,
                title=title,
                model=model,
                duration_s=duration_s,
                last_reply=last_reply,
                link=link,
                job_id=job_id,
            )
        if settings.discord_webhook_url:
            await _post_discord(
                client,
                url=settings.discord_webhook_url,
                title=title,
                model=model,
                duration_s=duration_s,
                last_reply=last_reply,
                link=link,
                job_id=job_id,
            )


def _job_title(job: dict) -> str:
    """Best-effort title: job metadata name, or first user message text."""
    metadata = job.get("metadata") or {}
    if isinstance(metadata, str):
        import json

        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if isinstance(metadata, dict) and metadata.get("name"):
        return str(metadata["name"])

    import json

    try:
        payload = json.loads(job.get("request_json") or "{}")
    except Exception:
        return f"Job {job.get('id', '')}"

    messages = payload.get("messages") or []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = " ".join(parts).strip()
        else:
            text = ""
        if text:
            return text[:80] + ("…" if len(text) > 80 else "")
    return f"Job {job.get('id', '')}"


def _duration_seconds(job: dict) -> float:
    started = job.get("started_at")
    finished = job.get("finished_at")
    if started is None or finished is None:
        return 0.0
    return max(0.0, (finished - started) / 1000.0)


def _last_reply_snippet(job_id: str) -> str:
    """Return the trailing 200 chars of the last assistant SSE text chunk."""
    events = datastore.list_agent_job_events(job_id)
    import json

    text_parts: list[str] = []
    for ev in events:
        if ev.get("event_type") != "sse":
            continue
        raw = ev.get("data") or ""
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except Exception:
                continue
            # Anthropic SSE: content_block_delta with type=text_delta
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text") or "")
    full_text = "".join(text_parts)
    if not full_text:
        return ""
    return full_text[-200:]


async def _post_slack(
    client: httpx.AsyncClient,
    *,
    url: str,
    title: str,
    model: str,
    duration_s: float,
    last_reply: str,
    link: str,
    job_id: str,
) -> None:
    summary = f"*{title}*\nModel: `{model}` | Duration: {duration_s:.0f}s"
    if last_reply:
        summary += f"\n>{last_reply}"
    payload: dict = {
        "text": f"Job finished: {title}",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": summary},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{link}|Open chat>",
                },
            },
        ],
    }
    try:
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "notifier: Slack webhook returned {} for job {}",
                resp.status_code,
                job_id,
            )
    except Exception as exc:
        logger.warning("notifier: Slack webhook failed for job {}: {}", job_id, exc)


async def _post_discord(
    client: httpx.AsyncClient,
    *,
    url: str,
    title: str,
    model: str,
    duration_s: float,
    last_reply: str,
    link: str,
    job_id: str,
) -> None:
    description = f"Model: `{model}` | Duration: {duration_s:.0f}s"
    if last_reply:
        description += f"\n\n{last_reply}"
    payload: dict = {
        "content": f"Job finished: **{title}** — [Open chat]({link})",
        "embeds": [
            {
                "title": title,
                "description": description,
                "url": link,
            }
        ],
    }
    try:
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "notifier: Discord webhook returned {} for job {}",
                resp.status_code,
                job_id,
            )
    except Exception as exc:
        logger.warning("notifier: Discord webhook failed for job {}: {}", job_id, exc)
