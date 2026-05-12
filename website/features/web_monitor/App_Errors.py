"""App_Errors — alert fan-out for FastAPI uncaught exceptions / 5xx.

One file, one Slack channel: `#app-errors`. Self-contained (its own Slack
posting helper) so it can be reasoned about without looking at siblings.

Wiring (already done in website/app.py):

    from website.features.web_monitor.App_Errors import notify_app_error
    from starlette.responses import JSONResponse

    @app.exception_handler(Exception)
    async def _on_unhandled(request, exc):
        await notify_app_error(
            route=request.url.path,
            exc_type=type(exc).__name__,
            message=str(exc)[:400],
            request_id=request.headers.get("x-request-id"),
        )
        return JSONResponse({"error": "internal_server_error"}, status_code=500)

This module has no inbound HTTP endpoint — app errors originate in our own
code, not from external webhooks. The only public surface is
`notify_app_error()` plus an optional `router` with a healthz check.

Env vars:
    SLACK_WEBHOOK_APP_ERRORS    # Slack incoming webhook URL for #app-errors
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter

from website.features.web_monitor._slack_client import post_with_retry

logger = logging.getLogger("website.web_monitor.app_errors")

router = APIRouter(prefix="/webhooks/monitor", tags=["web_monitor.app_errors"])

SLACK_ENV_VAR = "SLACK_WEBHOOK_APP_ERRORS"


# ---------------------------------------------------------------------------
# Slack posting
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SlackMessage:
    title: str
    body: str
    severity: str = "critical"         # info | warning | critical
    fields: dict[str, str] | None = None
    source: str = "app"

    def to_payload(self) -> dict[str, Any]:
        color = {
            "info": "#2E86AB",
            "warning": "#D4A024",
            "critical": "#C83E4D",
        }.get(self.severity, "#C83E4D")
        fields = [
            {"type": "mrkdwn", "text": f"*{k}*\n{v}"}
            for k, v in (self.fields or {}).items()
        ]
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": self.title}},
            {"type": "section", "text": {"type": "mrkdwn", "text": self.body}},
        ]
        if fields:
            blocks.append({"type": "section", "fields": fields[:10]})
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"source: `{self.source}` · severity: `{self.severity}`",
                    }
                ],
            }
        )
        return {"attachments": [{"color": color, "blocks": blocks}]}


async def post_to_app_errors(msg: SlackMessage) -> bool:
    """POST a Slack message to #app-errors. Returns True on 2xx.

    Never raises — a failed alert must not escalate into a failed response.
    WM-05: delegates to _slack_client.post_with_retry which honors Slack's
    Retry-After on 429 and applies exp+jitter backoff for transient errors.
    """
    url = os.getenv(SLACK_ENV_VAR)
    if not url:
        logger.warning(
            "app_errors: %s unset; alert logged only: %s", SLACK_ENV_VAR, msg.title
        )
        # Structured contract: level=info, channel name embedded.
        logger.info("ALERT[app_errors] %s — %s", msg.title, msg.body)
        return False
    response = await post_with_retry(url, msg.to_payload())
    if response is None:
        logger.error("app_errors: Slack post gave up after retries: %s", msg.title)
        return False
    if not (200 <= response.status_code < 300):
        # B-4: never log response.text — Slack echoes payload fragments that
        # may include PII / log-injection content. Status + reason + length
        # are sufficient for triage; body is fetched only at WARN level in
        # exceptional debug sessions, never in standard error logging.
        logger.error(
            "app_errors: Slack post failed status=%s reason=%s body_len=%s",
            response.status_code,
            response.reason_phrase,
            len(response.text),
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Public notifier
# ---------------------------------------------------------------------------


async def notify_app_error(
    *,
    route: str,
    exc_type: str,
    message: str,
    request_id: str | None = None,
) -> None:
    """Post an uncaught exception / 5xx description to #app-errors.

    Called from the FastAPI global exception handler. Never raises — the
    handler wraps this in its own try/except too, but we double up because
    alerting must never be in the critical response path.

    Args:
        route: request path (e.g. ``/api/summarize``).
        exc_type: class name of the exception (e.g. ``ValueError``).
        message: stringified exception — truncate before calling if huge.
        request_id: optional x-request-id header value for trace correlation.
    """
    msg = SlackMessage(
        title=f":boom: {exc_type} on {route}",
        body=f"```{message[:900]}```",
        severity="critical",
        fields={"request_id": request_id or "—"},
        source="app",
    )
    try:
        await post_to_app_errors(msg)
    except Exception:  # noqa: BLE001
        logger.exception("app_errors: notify_app_error dispatch failed")


# ---------------------------------------------------------------------------
# Healthz (no inbound webhook — just a config probe)
# ---------------------------------------------------------------------------


@router.get("/app-errors/healthz")
async def app_errors_healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "channel": "app_errors",
        "webhook_configured": bool(os.getenv(SLACK_ENV_VAR)),
    }


__all__ = [
    "router",
    "SlackMessage",
    "post_to_app_errors",
    "notify_app_error",
]
