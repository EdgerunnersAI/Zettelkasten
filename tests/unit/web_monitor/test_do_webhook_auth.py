"""WM-02 / WM-03: inbound /digitalocean webhook auth + HMAC compare-digest.

* WM-02: 401 without matching alert_uuid; 202 with match; 400 on malformed JSON.
* WM-03: hmac.compare_digest must be the comparator (timing-leak guard).
  We verify by monkeypatching ``hmac.compare_digest`` on the module under
  test and asserting it gets called — i.e. the surgical fix is in effect
  and a future regression that swaps to ``!=`` would fail this test.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from website.features.web_monitor import DO_Alerts as do_mod


@pytest.fixture
def client(monkeypatch, slack_webhook_mock):
    """A FastAPI app mounting only the DO_Alerts router, secret set."""
    slack_webhook_mock()
    monkeypatch.setenv("DO_ALERT_WEBHOOK_SECRET", "the-real-secret")
    app = FastAPI()
    app.include_router(do_mod.router)
    return TestClient(app)


def test_digitalocean_webhook_rejects_missing_alert_uuid(client):
    r = client.post("/webhooks/monitor/digitalocean", json={"trigger_metric": "cpu"})
    assert r.status_code == 401
    assert r.json()["detail"] == "bad alert_uuid"


def test_digitalocean_webhook_rejects_wrong_alert_uuid(client):
    r = client.post(
        "/webhooks/monitor/digitalocean",
        json={"alert_uuid": "wrong-secret", "trigger_metric": "cpu"},
    )
    assert r.status_code == 401


def test_digitalocean_webhook_accepts_matching_alert_uuid(client):
    r = client.post(
        "/webhooks/monitor/digitalocean",
        json={
            "alert_uuid": "the-real-secret",
            "trigger_metric": "cpu",
            "trigger_status": "alert",
            "value": 92.0,
        },
    )
    assert r.status_code == 202
    assert r.json()["status"] in {"delivered", "logged"}


def test_digitalocean_webhook_malformed_json_returns_400(client):
    r = client.post(
        "/webhooks/monitor/digitalocean",
        content=b"{not-json:",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert "invalid json" in r.json()["detail"]


def test_digitalocean_webhook_empty_body_treated_as_empty_object(client):
    # Empty body parses as `{}` — payload validation passes (all fields
    # optional) but alert_uuid is None → 401 when secret is required.
    r = client.post("/webhooks/monitor/digitalocean", content=b"")
    assert r.status_code == 401


def test_digitalocean_webhook_uses_compare_digest_not_eq(monkeypatch, client):
    """WM-03 surgical: assert hmac.compare_digest is invoked.

    Regression guard: if a future commit swaps back to ``!=``, this assertion
    fails. We patch the bound ``hmac`` attribute on the do_alerts module so
    the original implementation is unaffected for sibling tests.
    """
    calls: list[tuple[bytes, bytes]] = []
    real_compare = do_mod.hmac.compare_digest

    def _spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(do_mod.hmac, "compare_digest", _spy)

    r = client.post(
        "/webhooks/monitor/digitalocean",
        json={
            "alert_uuid": "the-real-secret",
            "trigger_metric": "cpu",
            "trigger_status": "alert",
        },
    )
    assert r.status_code == 202
    assert len(calls) == 1, (
        f"expected hmac.compare_digest to be called once, got {len(calls)}"
    )
    a, b = calls[0]
    assert isinstance(a, bytes) and isinstance(b, bytes)


def test_digitalocean_webhook_no_secret_env_skips_auth(monkeypatch, slack_webhook_mock):
    """When DO_ALERT_WEBHOOK_SECRET is unset, the endpoint accepts any uuid."""
    slack_webhook_mock()
    monkeypatch.delenv("DO_ALERT_WEBHOOK_SECRET", raising=False)
    app = FastAPI()
    app.include_router(do_mod.router)
    client = TestClient(app)
    r = client.post(
        "/webhooks/monitor/digitalocean",
        json={"alert_uuid": "anything", "trigger_metric": "cpu"},
    )
    assert r.status_code == 202
