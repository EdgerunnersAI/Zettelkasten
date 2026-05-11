"""UA-01: server-side smoke for `/auth/callback`.

The route serves a static HTML page that drives the OAuth code-exchange
flow client-side. The smoke test confirms the route is wired, the file
is reachable through FastAPI, and the response is well-formed HTML
containing the DOM hooks the JS bundle relies on.

Marked `@pytest.mark.live` is NOT applied here — this is a pure
server-side static-asset smoke that runs without any external service.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from website.app import create_app


@pytest.fixture
def client() -> TestClient:
    # Clear rate-limiter state between tests (parity with test_website.py).
    from website.api import routes

    routes._rate_store.clear()
    app = create_app()
    return TestClient(app)


class TestAuthCallbackRoute:
    def test_callback_returns_200(self, client: TestClient) -> None:
        resp = client.get("/auth/callback")
        assert resp.status_code == 200

    def test_callback_returns_html(self, client: TestClient) -> None:
        resp = client.get("/auth/callback")
        body = resp.text
        assert "<!DOCTYPE html>" in body or "<!doctype html>" in body.lower()
        assert "</html>" in body

    def test_callback_includes_required_dom_hooks(self, client: TestClient) -> None:
        """JS depends on these element ids — assert the static asset
        ships them so a future edit breaking the DOM contract fails
        loudly server-side too."""
        resp = client.get("/auth/callback")
        body = resp.text
        for element_id in ("spinner", "status", "error", "retry"):
            assert f'id="{element_id}"' in body, f"callback missing #{element_id}"

    def test_callback_loads_browser_cache_script(self, client: TestClient) -> None:
        """callback.html must include the browser_cache module so
        `consumeReturnPath()` is available when the inline script runs."""
        resp = client.get("/auth/callback")
        assert "/browser-cache/js/cache.js" in resp.text

    def test_callback_calls_supabase_exchange(self, client: TestClient) -> None:
        """The page must use the SDK's exchangeCodeForSession — the
        state-CSRF check rides on that call. Asserting at the
        served-bytes layer makes the test resilient to file moves."""
        resp = client.get("/auth/callback")
        assert "exchangeCodeForSession" in resp.text

    def test_callback_content_type_is_html(self, client: TestClient) -> None:
        resp = client.get("/auth/callback")
        ct = resp.headers.get("content-type", "")
        assert "html" in ct.lower(), f"unexpected content-type: {ct!r}"
