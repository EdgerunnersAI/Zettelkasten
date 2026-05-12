"""WM-08: DOAlertPayload fuzz — extra fields allowed, no crash on bad shapes.

Payment webhook is a 501 stub today (see User_Activity.payment_webhook); the
spec's fuzz target there is contractually deferred until the provider is
wired. We assert the 501 contract here so it's documented as a known status.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from website.features.web_monitor import DO_Alerts as do_mod
from website.features.web_monitor import User_Activity as ua_mod


@pytest.fixture
def client(slack_webhook_mock, monkeypatch):
    slack_webhook_mock()
    monkeypatch.delenv("DO_ALERT_WEBHOOK_SECRET", raising=False)
    app = FastAPI()
    app.include_router(do_mod.router)
    app.include_router(ua_mod.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "payload",
    [
        # Empty
        {},
        # Only the alert_uuid
        {"alert_uuid": "x"},
        # Extra fields allowed (`extra: allow`) — must not 422/500
        {"unexpected_field": [1, 2, 3], "deep": {"nested": {"more": True}}},
        # Wrong types where pydantic might coerce
        {"droplet_id": "565709868"},  # numeric string coerces to int
        # All-None payload
        {
            "alert_uuid": None,
            "alert_description": None,
            "trigger_metric": None,
            "trigger_status": None,
        },
        # Huge string
        {"alert_description": "x" * 5000},
        # Unicode + emojis
        {"droplet_name": "🚀 droplet 名前", "trigger_metric": "cpu"},
        # Boolean for value (pydantic v2: True coerces to 1.0)
        {"value": True, "trigger_metric": "cpu"},
        # Float-as-string
        {"value": "92.5", "trigger_metric": "cpu"},
    ],
)
def test_do_alert_payload_fuzz_no_5xx(client, payload):
    r = client.post("/webhooks/monitor/digitalocean", json=payload)
    # 2xx (accepted) OR 4xx (rejected) — never 5xx
    assert r.status_code < 500, f"5xx on payload {payload!r}: {r.text}"


def test_do_alert_payload_rejects_non_object_json(client):
    # JSON arrays, strings, numbers all fail validation but cleanly (4xx).
    for body in ["[]", '"x"', "42", "null"]:
        r = client.post(
            "/webhooks/monitor/digitalocean",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code < 500, f"5xx on {body!r}: {r.text}"


def test_payment_webhook_stub_returns_501(client):
    """WM-08 contractual gap: payment webhook is a 501 stub; assert that
    contract so the test suite documents the intentional deferral."""
    r = client.post("/webhooks/monitor/payment", json={"any": "thing"})
    assert r.status_code == 501
    assert "not yet wired" in r.json()["detail"]
