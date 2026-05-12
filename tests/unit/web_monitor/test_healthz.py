"""WM-12: healthz contract — 3 endpoints, fixed shape.

Each *_healthz returns {ok, channel, webhook_configured}; user-activity
also reports pricing_throttle_seen (the in-memory throttle map size).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from website.features.web_monitor import router as web_monitor_router


@pytest.fixture
def client(monkeypatch):
    # Toggle vars so webhook_configured exercises both branches across tests.
    for v in (
        "SLACK_WEBHOOK_APP_ERRORS",
        "SLACK_WEBHOOK_DO_ALERT",
        "SLACK_WEBHOOK_USER_ACTIVITY",
    ):
        monkeypatch.setenv(v, "https://hooks.slack.com/x/y/z")
    app = FastAPI()
    app.include_router(web_monitor_router)
    return TestClient(app)


def test_app_errors_healthz_shape(client):
    r = client.get("/webhooks/monitor/app-errors/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["channel"] == "app_errors"
    assert data["webhook_configured"] is True


def test_do_alerts_healthz_shape(client):
    r = client.get("/webhooks/monitor/digitalocean/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["channel"] == "do_alerts"
    assert data["webhook_configured"] is True


def test_user_activity_healthz_shape_includes_throttle_count(client):
    r = client.get("/webhooks/monitor/user-activity/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["channel"] == "user_activity"
    assert data["webhook_configured"] is True
    # User-activity-specific field — must be present, type int, >=0.
    assert "pricing_throttle_seen" in data
    assert isinstance(data["pricing_throttle_seen"], int)
    assert data["pricing_throttle_seen"] >= 0


def test_healthz_webhook_configured_false_when_env_unset(monkeypatch):
    for v in (
        "SLACK_WEBHOOK_APP_ERRORS",
        "SLACK_WEBHOOK_DO_ALERT",
        "SLACK_WEBHOOK_USER_ACTIVITY",
    ):
        monkeypatch.delenv(v, raising=False)
    app = FastAPI()
    app.include_router(web_monitor_router)
    client = TestClient(app)
    for path in (
        "/webhooks/monitor/app-errors/healthz",
        "/webhooks/monitor/digitalocean/healthz",
        "/webhooks/monitor/user-activity/healthz",
    ):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["webhook_configured"] is False
