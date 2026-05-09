"""Phase 4.2 v2 dual-path test for ``GET /api/me``.

Drives the real FastAPI test client with DB v2 forced on, mints a fresh user,
and asserts ``/api/me`` returns the user's email from ``core.profiles`` via
the new :class:`CoreRepository` v2 branch. A second test confirms the
unauthenticated path returns 401 (so the v2 branch is gated behind auth and
not silently leaking profile data).

Marked ``@pytest.mark.live`` because it touches the live v2 Supabase project
and the FastAPI app's startup wiring.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def v2_app(monkeypatch):
    """Build a fresh FastAPI app with DB v2 forced on for the test.

    Mirrors ``test_api_graph_v2.v2_app``: sets ``DB_SCHEMA_VERSION=v2`` BEFORE
    app construction so module-level routing reads v2, and resets the
    ``persist`` and ``auth`` module caches so JWKS + repos are reinitialised
    against the v2 endpoint for this test.
    """
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


def test_api_me_v2_returns_profile_email(v2_app, mint_user):
    """A v2 user hitting ``/api/me`` must get an ``email`` matching the
    ``e2e-{hex}@test.com`` pattern minted in :func:`mint_test_user_with_workspaces`,
    and the wire shape must keep the v1 keys (``id``, ``email``, ``name``,
    ``avatar_url``) so frontend callers don't break across the cutover."""
    user = mint_user(workspace_count=1)

    with TestClient(v2_app) as client:
        resp = client.get("/api/me", headers=_auth_headers(user.jwt))

    assert resp.status_code == 200, (
        f"v2 /api/me 5xx: status={resp.status_code} body={resp.text[:400]}"
    )
    body = resp.json()
    assert isinstance(body, dict), f"expected JSON object, got {type(body)!r}"
    # v1 wire-shape parity — all 4 keys present.
    for key in ("id", "email", "name", "avatar_url"):
        assert key in body, f"missing key {key!r} in /api/me response: {body!r}"
    # Profile email comes from core.profiles (seeded by mint_user trigger chain).
    assert body["email"].startswith("e2e-"), (
        f"expected e2e-* test email, got {body['email']!r}"
    )
    assert body["email"].endswith("@test.com"), (
        f"expected @test.com domain, got {body['email']!r}"
    )
    # id should match auth_user_id (== profile_id today).
    assert body["id"] == str(user.auth_user_id), (
        f"id={body['id']!r} != auth_user_id={user.auth_user_id!r}"
    )


def test_api_me_v2_unauthenticated_returns_401(v2_app):
    """No Authorization header must reject with 401 — v2 must not leak any
    profile field on the unauthenticated path."""
    with TestClient(v2_app) as client:
        resp = client.get("/api/me")
    assert resp.status_code == 401, (
        f"expected 401 without JWT, got status={resp.status_code} body={resp.text[:200]}"
    )
