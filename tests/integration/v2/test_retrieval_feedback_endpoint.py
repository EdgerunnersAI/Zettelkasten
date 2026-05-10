"""Phase 8.5.B-5 — live tests for /api/rag/feedback endpoint.

Verifies feedback events land in rag.retrieval_feedback_events for an authed
user, and 403 fires on cross-workspace attempts.
"""
from __future__ import annotations

import uuid

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


def _auth_headers(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def test_feedback_endpoint_inserts_event(v2_app, mint_user):
    user = mint_user(workspace_count=1)
    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/feedback",
            headers=_auth_headers(user.jwt),
            json={
                "event_type": "impression",
                "workspace_id": str(user.workspace_ids[0]),
                "rank_at_render": 3,
                "propensity_weight": 0.42,
                "attrs": {"src": "test-fixture"},
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert isinstance(body.get("event_id"), int)


def test_feedback_endpoint_blocks_other_workspace(v2_app, mint_user):
    user_a = mint_user(workspace_count=1)
    user_b = mint_user(workspace_count=1)
    with TestClient(v2_app) as client:
        # B writes a feedback event tagged to A's workspace → 403
        resp = client.post(
            "/api/rag/feedback",
            headers=_auth_headers(user_b.jwt),
            json={
                "event_type": "click",
                "workspace_id": str(user_a.workspace_ids[0]),
            },
        )
    assert resp.status_code == 403, resp.text


def test_feedback_endpoint_validates_event_type(v2_app, mint_user):
    user = mint_user(workspace_count=1)
    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/feedback",
            headers=_auth_headers(user.jwt),
            json={
                "event_type": "not_a_real_event",
                "workspace_id": str(user.workspace_ids[0]),
            },
        )
    assert resp.status_code == 422, resp.text


def test_feedback_endpoint_requires_auth(v2_app):
    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/feedback",
            json={
                "event_type": "impression",
                "workspace_id": str(uuid.uuid4()),
            },
        )
    assert resp.status_code in (401, 403), resp.text
