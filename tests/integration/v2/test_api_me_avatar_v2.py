"""Phase 8.5.R3 — PUT /api/me/avatar v2 port regression.

Asserts:
- The v2 handler writes to ``core.profiles.avatar_url`` (not the dropped
  ``public.kg_users``).
- The endpoint is self-only by JWT sub (no cross-tenant attack surface for
  this preset-picker shape — there's no path parameter / target-user input).
- B's PUT updates B's own profile, never A's.
- avatar_id ∈ [0, 59] preset → ``/artifacts/avatars/avatar_NN.svg`` URL.
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


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def test_v2_avatar_update_writes_to_profiles_avatar_url(v2_app, mint_user, asyncpg_pool):
    import asyncio
    user = mint_user(workspace_count=1)
    with TestClient(v2_app) as client:
        resp = client.put(
            "/api/me/avatar",
            headers=_auth(user.jwt),
            json={"avatar_id": 7},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["avatar_url"] == "/artifacts/avatars/avatar_07.svg"

    # Verify the v2 row actually changed (NOT a kg_users path side-effect).
    async def _read():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT avatar_url FROM core.profiles WHERE id = $1",
                user.auth_user_id,
            )
    avatar_url = asyncio.get_event_loop().run_until_complete(_read())
    assert avatar_url == "/artifacts/avatars/avatar_07.svg"


def test_v2_avatar_update_is_self_only(v2_app, mint_user, asyncpg_pool):
    """B's PUT updates B's profile, never A's. (No path param = no attack surface.)"""
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)

    # Seed A with a known avatar
    async def _set_a():
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE core.profiles SET avatar_url = $1 WHERE id = $2",
                "/artifacts/avatars/avatar_01.svg", a.auth_user_id,
            )
    asyncio.get_event_loop().run_until_complete(_set_a())

    # B updates own avatar
    with TestClient(v2_app) as client:
        resp = client.put(
            "/api/me/avatar",
            headers=_auth(b.jwt),
            json={"avatar_id": 42},
        )
    assert resp.status_code == 200, resp.text

    # Verify A unchanged, B updated
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            a_av = await conn.fetchval(
                "SELECT avatar_url FROM core.profiles WHERE id = $1", a.auth_user_id,
            )
            b_av = await conn.fetchval(
                "SELECT avatar_url FROM core.profiles WHERE id = $1", b.auth_user_id,
            )
            return a_av, b_av
    a_av, b_av = asyncio.get_event_loop().run_until_complete(_check())
    assert a_av == "/artifacts/avatars/avatar_01.svg", (
        f"A's avatar was modified by B's PUT! a_av={a_av!r}"
    )
    assert b_av == "/artifacts/avatars/avatar_42.svg", f"B's avatar not updated: {b_av!r}"


def test_v2_avatar_update_rejects_invalid_id(v2_app, mint_user):
    user = mint_user(workspace_count=1)
    with TestClient(v2_app) as client:
        # Out-of-range avatar_id → pydantic validator fails → 422
        resp = client.put(
            "/api/me/avatar",
            headers=_auth(user.jwt),
            json={"avatar_id": 999},
        )
    assert resp.status_code == 422, resp.text


def test_v2_avatar_update_requires_auth(v2_app):
    with TestClient(v2_app) as client:
        resp = client.put("/api/me/avatar", json={"avatar_id": 1})
    assert resp.status_code in (401, 403), resp.text
