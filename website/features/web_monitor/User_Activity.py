"""User_Activity — alert fan-out for conversion-funnel events.

One file, one Slack channel: `#user-activity`. Self-contained (own Slack
helper, own in-memory throttle, own router) matching the per-channel
convention already established by DO_Alerts.py and App_Errors.py.

Events surfaced:
    1. ``notify_new_signup(...)``      — first successful row insert into
       ``core.profiles`` (v2). Called from the v2 profile-bootstrap path
       the moment a brand-new user lands (OAuth or email signup, uniform
       path).
    2. ``notify_pricing_visit(...)``   — GET /pricing hit, throttled to
       one alert per IP per hour so bots / refresh-spam don't drown the
       channel.
    3. ``notify_payment(...)``         — payment success. Future. Fire
       from the provider webhook handler once Stripe/Razorpay is wired in.
       The ``/webhooks/monitor/payment`` stub endpoint below is the
       placeholder; flesh it out with signature verification when the
       provider is chosen.

Wiring (one-time):

    # website/core/supabase_v2/repositories/core_repository.py — after a new
    # core.profiles row has been inserted via ensure_profile().
    from website.features.web_monitor.User_Activity import notify_new_signup
    import asyncio
    asyncio.create_task(notify_new_signup(
        user_id=str(profile_id),
        email=email,
        display_name=display_name,
        render_user_id=render_user_id,
    ))

    # website/app.py, inside the /pricing route handler
    from website.features.web_monitor.User_Activity import notify_pricing_visit
    asyncio.create_task(notify_pricing_visit(request))

Env vars:
    SLACK_WEBHOOK_USER_ACTIVITY   # Slack incoming webhook URL
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status

logger = logging.getLogger("website.web_monitor.user_activity")

router = APIRouter(prefix="/webhooks/monitor", tags=["web_monitor.user_activity"])

SLACK_ENV_VAR = "SLACK_WEBHOOK_USER_ACTIVITY"

# Per-IP throttle for pricing-visit alerts. Dict[ip, last_alert_epoch].
# Bounded by _PRICING_THROTTLE_MAX to cap memory under scan/DoS traffic;
# when full we evict the oldest entry (O(n) scan is fine at n ≤ 2000).
_PRICING_THROTTLE_SECONDS = 60 * 60       # 1 alert / IP / hour
_PRICING_THROTTLE_MAX = 2000
_pricing_seen_at: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Slack posting
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SlackMessage:
    title: str
    body: str
    severity: str = "info"          # info | warning | critical
    fields: dict[str, str] | None = None
    source: str = "user_activity"

    def to_payload(self) -> dict[str, Any]:
        color = {
            "info": "#2E86AB",
            "warning": "#D4A024",
            "critical": "#C83E4D",
        }.get(self.severity, "#2E86AB")
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
                        "text": f"source: `{self.source}` · event: `{self.severity}`",
                    }
                ],
            }
        )
        return {"attachments": [{"color": color, "blocks": blocks}]}


async def post_to_user_activity(msg: SlackMessage) -> bool:
    """POST to #user-activity. Returns True on 2xx. Never raises."""
    url = os.getenv(SLACK_ENV_VAR)
    if not url:
        logger.warning(
            "user_activity: %s unset; event logged only: %s", SLACK_ENV_VAR, msg.title
        )
        logger.info("ALERT[user_activity] %s — %s", msg.title, msg.body)
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=msg.to_payload())
        if not (200 <= r.status_code < 300):
            logger.error(
                "user_activity: Slack post failed (%s): %s",
                r.status_code,
                r.text[:200],
            )
            return False
        return True
    except httpx.HTTPError as exc:
        logger.exception("user_activity: Slack post errored: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Return the real client IP.

    Cloudflare → Caddy → uvicorn. Caddy sets ``X-Forwarded-For`` preserving
    CF's first-hop IP; fall back to request.client.host if missing (which
    means we're behind an unexpected proxy chain or called directly).
    """
    fwd = request.headers.get("x-forwarded-for") or request.headers.get(
        "cf-connecting-ip"
    )
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _mask_email(email: str | None) -> str:
    """Redact email to ``a***@domain.tld`` so Slack doesn't leak PII."""
    if not email or "@" not in email:
        return email or "—"
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


# ---------------------------------------------------------------------------
# Event 1 — new signup
# ---------------------------------------------------------------------------


async def notify_new_signup(
    *,
    user_id: str,
    email: str | None = None,
    display_name: str | None = None,
    render_user_id: str | None = None,
    signup_source: str | None = None,
) -> None:
    """A new row just landed in ``core.profiles`` — celebrate in Slack.

    Called from the v2 profile-bootstrap path immediately after the
    INSERT into ``core.profiles`` succeeds. Never called on subsequent
    logins (that path returns early on the SELECT branch).

    Args:
        user_id: our internal Supabase UUID (primary key of core.profiles).
        email: supplied by Supabase auth metadata; will be masked in Slack.
        display_name: OAuth provider display name if any.
        render_user_id: Supabase auth.users id (the ``sub`` from the JWT).
        signup_source: free-form hint ("oauth:google", "email", …) if the
            caller has it. Optional.
    """
    fields = {
        "user_id": user_id[:8] + "…",
        "email": _mask_email(email),
    }
    if display_name:
        fields["name"] = display_name
    if render_user_id:
        fields["auth_id"] = render_user_id[:8] + "…"
    if signup_source:
        fields["source"] = signup_source

    msg = SlackMessage(
        title=":tada: New signup",
        body=f"A new user just joined — {_mask_email(email)}",
        severity="info",
        fields=fields,
        source="signup",
    )
    try:
        await post_to_user_activity(msg)
    except Exception:  # noqa: BLE001 — alerting must never break signup
        logger.exception("user_activity: notify_new_signup dispatch failed")


# ---------------------------------------------------------------------------
# Event 2 — pricing page visit
# ---------------------------------------------------------------------------


async def notify_pricing_visit(request: Request) -> None:
    """GET /pricing fired — throttled to 1 alert per IP per hour.

    The throttle is in-memory, so each container replica tracks its own
    map. That's a feature: blue/green each send at most one alert per IP
    per hour, which caps Slack noise at ~2 alerts/hour in the worst case
    (during a cutover).
    """
    ip = _client_ip(request)
    now = time.time()

    last = _pricing_seen_at.get(ip)
    if last is not None and (now - last) < _PRICING_THROTTLE_SECONDS:
        return  # throttled

    # Bound the map before insert.
    if len(_pricing_seen_at) >= _PRICING_THROTTLE_MAX:
        oldest_ip = min(_pricing_seen_at, key=_pricing_seen_at.get)  # type: ignore[arg-type]
        _pricing_seen_at.pop(oldest_ip, None)
    _pricing_seen_at[ip] = now

    ua = (request.headers.get("user-agent") or "—")[:120]
    referer = request.headers.get("referer") or "—"
    country = request.headers.get("cf-ipcountry") or "—"  # Cloudflare geo hint

    msg = SlackMessage(
        title=":eyes: Pricing page visit",
        body=f"Someone is checking out the pricing page from *{country}*",
        severity="info",
        fields={
            "ip": ip,
            "country": country,
            "referer": referer[:200],
            "user_agent": ua,
        },
        source="pricing",
    )
    try:
        await post_to_user_activity(msg)
    except Exception:  # noqa: BLE001
        logger.exception("user_activity: notify_pricing_visit dispatch failed")


# ---------------------------------------------------------------------------
# Event 3 — payment (future)
# ---------------------------------------------------------------------------


async def notify_payment(
    *,
    user_id: str | None,
    email: str | None,
    amount: float,
    currency: str = "INR",
    plan: str | None = None,
    provider: str = "unknown",
    provider_payment_id: str | None = None,
) -> None:
    """Payment succeeded. Wire this into the eventual provider webhook
    handler (Stripe ``payment_intent.succeeded``, Razorpay ``payment.captured``
    — whichever is chosen).

    Left as a callable now so the hook site in /webhooks/monitor/payment
    below can be filled in later without touching this file's public API.
    """
    msg = SlackMessage(
        title=f":moneybag: Payment — {amount:.2f} {currency}",
        body=f"*{_mask_email(email)}* just paid {amount:.2f} {currency}"
        + (f" for *{plan}*" if plan else ""),
        severity="info",
        fields={
            "user_id": (user_id or "—")[:8] + ("…" if user_id else ""),
            "provider": provider,
            "provider_payment_id": provider_payment_id or "—",
            "plan": plan or "—",
        },
        source="payment",
    )
    try:
        await post_to_user_activity(msg)
    except Exception:  # noqa: BLE001
        logger.exception("user_activity: notify_payment dispatch failed")


# ---------------------------------------------------------------------------
# Future payment webhook (stub — provider-agnostic placeholder)
# ---------------------------------------------------------------------------


@router.post("/payment", status_code=status.HTTP_202_ACCEPTED)
async def payment_webhook(request: Request) -> dict[str, str]:
    """Future: receive Stripe/Razorpay webhook → notify_payment().

    Left as a stub returning 501 until the payment provider is wired in.
    When flipping this on:
      1. Verify the provider's signature header (Stripe-Signature /
         X-Razorpay-Signature) using the provider's webhook secret from
         env (``STRIPE_WEBHOOK_SECRET`` / ``RAZORPAY_WEBHOOK_SECRET``).
      2. Accept only the success event type(s) you actually care about.
      3. Extract ``user_id``, ``email``, ``amount``, ``currency``,
         ``plan``, ``provider_payment_id`` from the payload.
      4. Call ``await notify_payment(...)``.

    Keep the 401/400 paths strict — payment webhooks are an attractive
    target for spoofing "fake success" messages to Slack.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="payment webhook not yet wired — provider TBD",
    )


# ---------------------------------------------------------------------------
# Healthz
# ---------------------------------------------------------------------------


@router.get("/user-activity/healthz")
async def user_activity_healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "channel": "user_activity",
        "webhook_configured": bool(os.getenv(SLACK_ENV_VAR)),
        "pricing_throttle_seen": len(_pricing_seen_at),
    }


__all__ = [
    "router",
    "SlackMessage",
    "post_to_user_activity",
    "notify_new_signup",
    "notify_pricing_visit",
    "notify_payment",
]
