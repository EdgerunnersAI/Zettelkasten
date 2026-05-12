"""WM-01 (integration): the global FastAPI exception handler dispatches the
alert and returns a clean 500 JSON, even when the alert helper raises.

We mount the production app with a single test-only route that raises an
unhandled exception, then monkeypatch ``notify_app_error`` (resolved on the
``website.app`` module) to a synthetic-raising stub. The handler must:

  1. Catch the synthetic alert-side error inside its own try/except
  2. Return a 500 with the documented JSON body
  3. NOT propagate either the original exception OR the alert-side exception
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


def _env_stub(monkeypatch):
    """Stub the env knobs required by the production app factory to boot."""
    monkeypatch.setenv("GEMINI_API_KEY", "ci-stub")
    monkeypatch.setenv("SUPABASE_V2_URL", "https://ci-stub.supabase.co")
    monkeypatch.setenv("SUPABASE_V2_ANON_KEY", "ci-stub-anon")
    monkeypatch.setenv("SUPABASE_V2_SERVICE_ROLE_KEY", "ci-stub-service")
    monkeypatch.setenv(
        "NEXUS_TOKEN_ENCRYPTION_KEY",
        "7TgtMgeR5dMTnXxW6ULICwhf66A1VpzwuNFuIBqmoe4=",
    )


@pytest.fixture
def app_with_failing_route(monkeypatch):
    _env_stub(monkeypatch)
    from website.app import create_app

    app = create_app()

    @app.get("/_test/boom")
    async def _boom():
        raise ValueError("synthetic test failure")

    return app


def test_global_exception_returns_500_when_alert_succeeds(app_with_failing_route, monkeypatch):
    # Stub notify_app_error so we don't touch the network.
    sent: list[dict] = []

    async def _stub_notify(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("website.app.notify_app_error", _stub_notify)

    client = TestClient(app_with_failing_route, raise_server_exceptions=False)
    r = client.get("/_test/boom")
    assert r.status_code == 500
    assert r.json() == {"error": "internal_server_error"}
    assert len(sent) == 1
    assert sent[0]["route"] == "/_test/boom"
    assert sent[0]["exc_type"] == "ValueError"


def test_global_exception_returns_500_when_alert_itself_raises(
    app_with_failing_route, monkeypatch
):
    """Alert dispatch raising must NOT cascade into a different status code."""

    async def _alert_boom(**_):
        raise RuntimeError("synthetic alert-side failure")

    monkeypatch.setattr("website.app.notify_app_error", _alert_boom)

    client = TestClient(app_with_failing_route, raise_server_exceptions=False)
    r = client.get("/_test/boom")
    assert r.status_code == 500
    assert r.json() == {"error": "internal_server_error"}
