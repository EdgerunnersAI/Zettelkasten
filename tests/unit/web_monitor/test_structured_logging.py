"""WM-10: every post_to_* path emits a structured log line on all branches.

"Structured" per Phase 0 § WM-10 = "named logger + level + message", not JSON.
We assert (a) the channel-scoped logger is used (not root) and (b) the level
matches the path: warning for missing env, error for non-2xx, exception for
transport-level failure.
"""
from __future__ import annotations

import logging

import httpx
import pytest
import respx

from website.features.web_monitor import App_Errors as ae_mod
from website.features.web_monitor import DO_Alerts as do_mod
from website.features.web_monitor import User_Activity as ua_mod
from website.features.web_monitor.App_Errors import (
    SlackMessage as AE_SlackMessage,
    post_to_app_errors,
)
from website.features.web_monitor.DO_Alerts import post_to_do_alerts
from website.features.web_monitor.User_Activity import post_to_user_activity


@pytest.fixture(autouse=True)
def _stamina_test_mode():
    import stamina
    stamina.set_testing(True, attempts=1)
    yield
    stamina.set_testing(False)


# ---------------------------------------------------------------------------
# Unset env → warning + log line, no Slack post attempted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_errors_unset_env_warns(monkeypatch, caplog):
    monkeypatch.delenv("SLACK_WEBHOOK_APP_ERRORS", raising=False)
    msg = AE_SlackMessage(title="t", body="b")
    with caplog.at_level(logging.WARNING, logger="website.web_monitor.app_errors"):
        ok = await post_to_app_errors(msg)
    assert ok is False
    assert any(
        r.name == "website.web_monitor.app_errors" and r.levelno == logging.WARNING
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_do_alerts_unset_env_warns(monkeypatch, caplog):
    monkeypatch.delenv("SLACK_WEBHOOK_DO_ALERT", raising=False)
    msg = do_mod.SlackMessage(title="t", body="b")
    with caplog.at_level(logging.WARNING, logger="website.web_monitor.do_alerts"):
        ok = await post_to_do_alerts(msg)
    assert ok is False
    assert any(
        r.name == "website.web_monitor.do_alerts" and r.levelno == logging.WARNING
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_user_activity_unset_env_warns(monkeypatch, caplog):
    monkeypatch.delenv("SLACK_WEBHOOK_USER_ACTIVITY", raising=False)
    msg = ua_mod.SlackMessage(title="t", body="b")
    with caplog.at_level(logging.WARNING, logger="website.web_monitor.user_activity"):
        ok = await post_to_user_activity(msg)
    assert ok is False
    assert any(
        r.name == "website.web_monitor.user_activity" and r.levelno == logging.WARNING
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Non-2xx → error log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_errors_non_2xx_logs_error(monkeypatch, caplog):
    monkeypatch.setenv("SLACK_WEBHOOK_APP_ERRORS", "https://hooks.slack.com/x/y/z")
    msg = AE_SlackMessage(title="t", body="b")
    with respx.mock:
        respx.post("https://hooks.slack.com/x/y/z").mock(
            return_value=httpx.Response(404, text="webhook revoked")
        )
        with caplog.at_level(logging.ERROR, logger="website.web_monitor.app_errors"):
            ok = await post_to_app_errors(msg)
    assert ok is False
    err_records = [
        r for r in caplog.records
        if r.name == "website.web_monitor.app_errors" and r.levelno == logging.ERROR
    ]
    assert err_records, "expected an ERROR log on non-2xx Slack response"
