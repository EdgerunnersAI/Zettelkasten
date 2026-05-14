"""Tests for the website API routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from website.app import create_app


@pytest.fixture
def client():
    # Clear rate limiter state between tests
    from website.api import zettels_routes
    zettels_routes._RATE_STORE.clear()
    zettels_routes._IDEMPOTENCY_CACHE.clear()

    app = create_app()
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestIndexPage:
    def test_index_returns_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Zettelkasten" in resp.text


class TestAddZettelEndpoint:
    def test_missing_url_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/zettels/add", json={})
        assert resp.status_code == 422

    def test_invalid_url_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/zettels/add",
            json={"url": "not-a-url", "client_action_id": "a", "surface": "landing"},
        )
        assert resp.status_code == 422

    def test_empty_url_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/zettels/add",
            json={"url": "", "client_action_id": "a", "surface": "landing"},
        )
        assert resp.status_code == 422


class TestRateLimit:
    def test_rate_limit_enforced(self, client: TestClient, monkeypatch) -> None:
        """After 10 requests in quick succession, the 11th should be rate-limited."""
        from website.api import zettels_routes

        async def fake_run(body, *, user, effective_user_id):
            return {
                "status": "succeeded",
                "operation_id": body.client_action_id,
                "summary": None,
                "persistence": {
                    "requested": True,
                    "persisted": False,
                    "file_store": False,
                    "supabase": False,
                    "duplicate": False,
                },
                "quality": {"confidence": "test", "confidence_reason": None, "quality_signals": {}},
                "node_id": None,
                "workspace_zettel_id": None,
                "status_url": None,
            }

        monkeypatch.setattr(zettels_routes, "_run_add_zettel", fake_run)
        for i in range(10):
            resp = client.post(
                "/api/zettels/add",
                json={
                    "url": f"https://example.com/{i}",
                    "client_action_id": f"a-{i}",
                    "surface": "landing",
                },
            )
            assert resp.status_code == 200

        resp = client.post(
            "/api/zettels/add",
            json={
                "url": "https://example.com/limited",
                "client_action_id": "a-11",
                "surface": "landing",
            },
        )
        assert resp.status_code == 429
