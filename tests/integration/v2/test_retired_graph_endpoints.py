"""Phase 8.5.R3 / Phase 8 Task 4c+4d — retired graph endpoints regression.

Asserts:
- POST /api/graph/search → 410 Gone with Sunset + Deprecation headers
- POST /api/graph/rebuild-links → 404 (route deleted)
- POST /api/graph/query → 410 Gone (already retired in 8.5.C-defer, fd6e2fd)

These are the retirement endpoints; the cross-tenant safety property is
"no v1-schema query is executed under any JWT" — 410/404 satisfy it
deterministically without exercising the v1 fallback paths.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def v2_app(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.app import create_app
    return create_app()


def test_graph_search_returns_410_gone(v2_app):
    with TestClient(v2_app) as client:
        resp = client.post("/api/graph/search", json={"query": "test"})
    assert resp.status_code == 410, resp.text
    body = resp.json()
    assert body.get("error") == "gone"
    assert "Sunset" in resp.headers
    assert "Deprecation" in resp.headers


def test_graph_query_returns_410_gone(v2_app):
    with TestClient(v2_app) as client:
        resp = client.post("/api/graph/query", json={"question": "what?"})
    assert resp.status_code == 410, resp.text
    body = resp.json()
    assert body.get("error") == "gone"
    assert "Sunset" in resp.headers


def test_graph_rebuild_links_route_deleted(v2_app):
    """Hard-deleted admin route returns FastAPI's default 404."""
    with TestClient(v2_app) as client:
        resp = client.post("/api/graph/rebuild-links")
    assert resp.status_code == 404, resp.text
