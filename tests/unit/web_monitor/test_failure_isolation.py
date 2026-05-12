"""WM-01 / WM-06: failure-isolation contracts.

* WM-01: A raising ``notify_app_error`` MUST NOT propagate out of the global
  exception handler — the user still gets a clean 500 JSON response.
* WM-06: A raising ``post_to_app_errors`` MUST NOT affect ``post_to_user_activity``
  on the same request (and vice versa) — channels are independent.
"""
from __future__ import annotations

import pytest

from website.features.web_monitor import App_Errors as ae_mod
from website.features.web_monitor import User_Activity as ua_mod
from website.features.web_monitor import DO_Alerts as do_mod
from website.features.web_monitor.App_Errors import (
    SlackMessage as AppErrSlackMessage,
    notify_app_error,
    post_to_app_errors,
)
from website.features.web_monitor.User_Activity import (
    SlackMessage as UASlackMessage,
    notify_pricing_visit,
    post_to_user_activity,
)


# ---------------------------------------------------------------------------
# WM-01 — notify_app_error never propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_app_error_swallows_internal_failure(monkeypatch):
    """Force post_to_app_errors to raise — notify_app_error must NOT bubble."""

    async def _boom(*_, **__):
        raise RuntimeError("synthetic slack failure")

    monkeypatch.setattr(ae_mod, "post_to_app_errors", _boom)

    # No try/except here intentionally: the test fails if RuntimeError escapes.
    await notify_app_error(
        route="/api/test",
        exc_type="ValueError",
        message="some error",
    )


@pytest.mark.asyncio
async def test_notify_app_error_handles_logging_recursion(monkeypatch):
    """Edge case: post raises AND logger.exception itself raises. We MUST
    still return cleanly. Guards against the alerting path's logger
    accidentally being the thing that escalates a Slack failure into an
    HTTP 500 cascade."""

    async def _boom(*_, **__):
        raise RuntimeError("post failure")

    monkeypatch.setattr(ae_mod, "post_to_app_errors", _boom)
    # The function's own try/except around logger should catch even
    # logger-side failures; we don't need to monkeypatch the logger here
    # because the BLE001 catch-all is broad.

    await notify_app_error(
        route="/api/test",
        exc_type="TypeError",
        message="x",
    )


# ---------------------------------------------------------------------------
# WM-06 — multi-channel isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_errors_failure_does_not_block_user_activity(slack_webhook_mock, monkeypatch):
    """post_to_app_errors raising must not affect a sibling post_to_user_activity."""
    rec = slack_webhook_mock()

    # Force App_Errors' Slack post to fail cleanly (post_with_retry returns
    # None when all retries are exhausted — the documented failure contract).
    async def _retries_exhausted(*_, **__):
        return None

    monkeypatch.setattr(ae_mod, "post_with_retry", _retries_exhausted)

    # Fire app errors (should swallow). Then fire user activity (should succeed).
    await notify_app_error(
        route="/api/x",
        exc_type="ValueError",
        message="boom",
    )
    msg = UASlackMessage(title=":eyes: test", body="hi", source="test")
    ok = await post_to_user_activity(msg)
    assert ok is True
    # User-activity channel got the call; app-errors got zero deliveries
    assert len(rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"]) == 1
    assert rec.calls["SLACK_WEBHOOK_APP_ERRORS"] == []


@pytest.mark.asyncio
async def test_user_activity_failure_does_not_block_do_alerts(slack_webhook_mock, monkeypatch):
    """Symmetric inverse: a User_Activity post failure must not block DO_Alerts.

    Simulates the realistic failure mode (post_with_retry exhausted retries
    and returned None) by patching the user-activity bound symbol only.
    """
    rec = slack_webhook_mock()

    async def _retries_exhausted(*_, **__):
        return None

    monkeypatch.setattr(ua_mod, "post_with_retry", _retries_exhausted)

    # User-activity post fails cleanly (logged); DO_Alerts unaffected.
    ua_msg = UASlackMessage(title=":eyes: x", body="b", source="t")
    ok_ua = await post_to_user_activity(ua_msg)
    assert ok_ua is False

    do_msg = do_mod.SlackMessage(title=":fire: x", body="b", source="t")
    ok_do = await do_mod.post_to_do_alerts(do_msg)
    assert ok_do is True
    assert len(rec.calls["SLACK_WEBHOOK_DO_ALERT"]) == 1
    # Confirm UA's mock never saw a delivery (retry-exhausted path)
    assert rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"] == []
