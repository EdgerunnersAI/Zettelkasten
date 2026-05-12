"""DO_Alerts — alert fan-out for DigitalOcean droplet monitoring.

One file, one Slack channel: `#do-alerts`. Self-contained (its own Slack
posting helper, its own router) so it can be reasoned about without looking
at siblings.

Channel wiring:
    The 3 DO alert policies (CPU > 80 %, Memory > 85 %, Disk > 80 %) post
    **direct** to Slack via DO's native `notifications.slack` integration —
    that path does not go through our app, so alerts still fire if
    zettelkasten.in itself is the thing that's down. The webhook endpoint
    below exists as a backup / manual path in case we ever want to route
    DO → our app → Slack (e.g. for enrichment, cross-channel routing, or
    if DO's Slack integration breaks).

Mount (already done in website/app.py):
    from website.features.web_monitor.DO_Alerts import router as do_alerts_router
    app.include_router(do_alerts_router)

Env vars:
    SLACK_WEBHOOK_DO_ALERT       # Slack incoming webhook URL for #do-alerts
    DO_ALERT_WEBHOOK_SECRET      # shared secret DO must include as
                                 # `alert_uuid` in payload; blank disables
                                 # auth (fine for low-profile URLs)
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from website.features.web_monitor._slack_client import post_with_retry

logger = logging.getLogger("website.web_monitor.do_alerts")

router = APIRouter(prefix="/webhooks/monitor", tags=["web_monitor.do_alerts"])

SLACK_ENV_VAR = "SLACK_WEBHOOK_DO_ALERT"


# ---------------------------------------------------------------------------
# Slack posting
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SlackMessage:
    title: str
    body: str
    severity: str = "warning"          # info | warning | critical
    fields: dict[str, str] | None = None
    source: str = "digitalocean"

    def to_payload(self) -> dict[str, Any]:
        color = {
            "info": "#2E86AB",
            "warning": "#D4A024",
            "critical": "#C83E4D",
        }.get(self.severity, "#D4A024")
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


async def post_to_do_alerts(msg: SlackMessage) -> bool:
    """POST a Slack message to #do-alerts. Returns True on 2xx.

    WM-05: delegates to _slack_client.post_with_retry for backoff handling.
    """
    url = os.getenv(SLACK_ENV_VAR)
    if not url:
        logger.warning(
            "do_alerts: %s unset; alert logged only: %s", SLACK_ENV_VAR, msg.title
        )
        logger.info("ALERT[do_alerts] %s — %s", msg.title, msg.body)
        return False
    response = await post_with_retry(url, msg.to_payload())
    if response is None:
        logger.error("do_alerts: Slack post gave up after retries: %s", msg.title)
        return False
    if not (200 <= response.status_code < 300):
        # B-4: drop response.text — Slack body may echo PII / log-injection.
        logger.error(
            "do_alerts: Slack post failed status=%s reason=%s body_len=%s",
            response.status_code,
            response.reason_phrase,
            len(response.text),
        )
        return False
    return True


# ---------------------------------------------------------------------------
# DigitalOcean monitoring webhook schema
# ---------------------------------------------------------------------------
# Representative DO payload (fields are all optional; schema is unversioned):
# {
#   "alert_id": "...",
#   "alert_uuid": "<secret we verify against>",
#   "alert_description": "CPU Utilization > 80%",
#   "trigger_metric": "cpu",
#   "trigger_status": "alert" | "resolved",
#   "droplet_id": 565709868,
#   "droplet_name": "Zettelkasten-Intel2GB",
#   "value": 91.2,
#   "region": "blr1",
#   "timestamp": "2026-04-21T14:05:00Z"
# }


class DOAlertPayload(BaseModel):
    alert_uuid: str | None = Field(default=None)
    alert_description: str | None = Field(default=None)
    trigger_metric: str | None = Field(default=None)
    trigger_status: str | None = Field(default=None)
    droplet_name: str | None = Field(default=None)
    droplet_id: int | None = Field(default=None)
    region: str | None = Field(default=None)
    value: float | None = Field(default=None)
    timestamp: str | None = Field(default=None)

    model_config = {"extra": "allow"}


def _severity(metric: str | None, status_: str | None, value: float | None) -> str:
    if status_ == "resolved":
        return "info"
    if value is None or metric is None:
        return "warning"
    if metric in {"cpu", "memory", "mem", "disk"} and value >= 95:
        return "critical"
    return "warning"


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@router.post("/digitalocean", status_code=status.HTTP_202_ACCEPTED)
async def digitalocean_alert(request: Request) -> dict[str, str]:
    """DO monitoring webhook → Slack #do-alerts (backup path)."""
    raw = await request.body()
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    # WM-08: cap validation failures at 400 so fuzz / malformed JSON never
    # escalates into a 5xx (non-object roots like list/string/number arrive
    # as dicts after json.loads but fail model validation).
    try:
        payload = DOAlertPayload.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"invalid payload: {exc.errors()[:3]}") from exc

    expected = os.getenv("DO_ALERT_WEBHOOK_SECRET")
    # WM-03 surgical: constant-time compare blocks timing side-channels that
    # leak shared-secret prefix length to an attacker iterating UUIDs.
    if expected and not hmac.compare_digest(
        (payload.alert_uuid or "").encode("utf-8"),
        expected.encode("utf-8"),
    ):
        logger.warning("do_alerts: webhook rejected — alert_uuid mismatch")
        raise HTTPException(status_code=401, detail="bad alert_uuid")

    metric = (payload.trigger_metric or "unknown").lower()
    status_ = (payload.trigger_status or "alert").lower()
    emoji = {
        "cpu": ":fire:",
        "memory": ":battery:",
        "mem": ":battery:",
        "disk": ":floppy_disk:",
    }.get(metric, ":rotating_light:")
    if status_ == "resolved":
        emoji = ":white_check_mark:"

    msg = SlackMessage(
        title=f"{emoji} DO alert — {payload.alert_description or metric} [{status_}]",
        body=(
            f"*Droplet:* `{payload.droplet_name or payload.droplet_id or 'unknown'}` "
            f"({payload.region or 'n/a'})\n"
            f"*Metric:* `{metric}`  *Value:* "
            f"`{payload.value if payload.value is not None else 'n/a'}`"
        ),
        severity=_severity(metric, status_, payload.value),
        fields={
            "timestamp": payload.timestamp or "—",
            "droplet_id": str(payload.droplet_id or "—"),
        },
        source="digitalocean",
    )
    delivered = await post_to_do_alerts(msg)
    return {"status": "delivered" if delivered else "logged"}


@router.get("/digitalocean/healthz")
async def do_alerts_healthz() -> dict[str, Any]:
    """Liveness + whether the Slack webhook URL is wired."""
    return {
        "ok": True,
        "channel": "do_alerts",
        "webhook_configured": bool(os.getenv(SLACK_ENV_VAR)),
    }


__all__ = [
    "router",
    "SlackMessage",
    "post_to_do_alerts",
]
