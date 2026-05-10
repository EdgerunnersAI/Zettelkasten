"""Phase 8.5.R3 — cross-tenant denial parametrized test.

Asserts that user B cannot access user A's resources through the data API.
Pattern proven on test_sandbox_routes_v2_cross_tenant_denial; this file
extends it across the next-most-sensitive handlers: chat sessions, adhoc
RAG, and zettel CRUD.

For PATCH/DELETE on RLS-blocked rows: Supabase returns 200 with empty data
(silent-fail trap). We assert the row is UNCHANGED in DB after, not just
the HTTP status.

Scope this turn: 5 route handlers covered. Deferred cases (PUT /api/me/avatar
v2 port, /api/graph/{query,search,rebuild-links} retirement) require Phase 8
Task 4 to fully land first; tracked in plan 8.5.R3 follow-up.
"""
from __future__ import annotations

import uuid

import asyncpg
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


# ---------------------------------------------------------------------------
# Seed helpers (service-role asyncpg bypasses RLS for fixture setup)
# ---------------------------------------------------------------------------
async def _seed_session(pool: asyncpg.Pool, *, workspace_id: uuid.UUID, profile_id: uuid.UUID) -> uuid.UUID:
    sid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.chat_sessions (id, workspace_id, profile_id, title) "
            "VALUES ($1, $2, $3, $4)",
            sid, workspace_id, profile_id, "cross-tenant denial fixture",
        )
    return sid


async def _seed_kasten(pool: asyncpg.Pool, *, workspace_id: uuid.UUID) -> uuid.UUID:
    """Insert a Kasten directly via service-role asyncpg; return its id."""
    kid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.kastens (id, workspace_id, name) VALUES ($1, $2, $3)",
            kid, workspace_id, f"xtenant-kasten-{uuid.uuid4().hex[:8]}",
        )
    return kid


async def _seed_workspace_zettel(pool: asyncpg.Pool, *, workspace_id: uuid.UUID) -> uuid.UUID:
    cz = uuid.uuid4()
    wz = uuid.uuid4()
    norm_url = f"https://xtenant-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO content.canonical_zettels "
            "(id, normalized_url, content_hash, source_type, title, body_md, publication_date) "
            "VALUES ($1, $2, $3, 'web', $4, 'body', '2026-04-01'::date)",
            cz, norm_url, chash, "xtenant denial fixture",
        )
        await conn.execute(
            "INSERT INTO content.workspace_zettels "
            "(id, workspace_id, canonical_zettel_id, ai_summary, user_tags, user_note, pinned, added_via) "
            "VALUES ($1, $2, $3, $4, $5, NULL, false, 'website')",
            wz, workspace_id, cz,
            '{"brief_summary": "x", "detailed_summary": "x"}',
            ["xtenant"],
        )
    return wz


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_session_get_cross_tenant_denied(v2_app, mint_user, asyncpg_pool):
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(asyncpg_pool, workspace_id=a.workspace_ids[0], profile_id=a.auth_user_id)
    )
    with TestClient(v2_app) as client:
        resp = client.get(f"/api/rag/sessions/{sid}", headers=_auth(b.jwt))
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env (e.g. LANGFUSE keys missing)")
    assert resp.status_code in (403, 404), resp.text


def test_session_delete_cross_tenant_denied(v2_app, mint_user, asyncpg_pool):
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(asyncpg_pool, workspace_id=a.workspace_ids[0], profile_id=a.auth_user_id)
    )
    with TestClient(v2_app) as client:
        resp = client.delete(f"/api/rag/sessions/{sid}", headers=_auth(b.jwt))
    # Read row back: must still exist
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM rag.chat_sessions WHERE id = $1", sid
            )
    surviving = asyncio.get_event_loop().run_until_complete(_check())
    assert surviving == 1, (
        f"User B's DELETE must NOT have removed user A's session "
        f"(http={resp.status_code}, surviving rows={surviving})"
    )


def test_adhoc_with_other_users_session_id_denied(v2_app, mint_user, asyncpg_pool):
    """Adhoc RAG with session_id pointing at another user's session must not leak."""
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(asyncpg_pool, workspace_id=a.workspace_ids[0], profile_id=a.auth_user_id)
    )
    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/adhoc",
            headers=_auth(b.jwt),
            json={"content": "tell me about secrets", "session_id": str(sid)},
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env (e.g. LANGFUSE keys missing)")
    # ANY non-200 response means no data leak occurred (the safety property).
    # 402 quota_exhausted is the documented fail-open-quota path (operator-locked
    # in feedback_pricing_module_authority); 403/404 are the explicit denials.
    if resp.status_code == 200:
        body_text = resp.text
        # No leak: A's email pattern (e2e-{hex}@test.com) must not appear in response
        assert a.email not in body_text, "user A's email leaked in B's adhoc response"
        # OWASP API1:2023 BOLA — UUID canaries catch leaks where A's data appears
        # without the email field (raw chunks, summaries, IDs).
        assert str(a.auth_user_id) not in body_text, "cross-tenant leak: A's auth_user_id in B's response"
        assert str(a.profile_id) not in body_text, "cross-tenant leak: A's profile_id in B's response"
        for ws_id in a.workspace_ids:
            assert str(ws_id) not in body_text, f"cross-tenant leak: A's workspace_id {ws_id} in B's response"
    else:
        assert resp.status_code in (402, 403, 404), resp.text


def test_zettel_patch_cross_tenant_denied(v2_app, mint_user, asyncpg_pool):
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    wz = asyncio.get_event_loop().run_until_complete(
        _seed_workspace_zettel(asyncpg_pool, workspace_id=a.workspace_ids[0])
    )
    with TestClient(v2_app) as client:
        resp = client.patch(
            f"/api/zettels/{wz}",
            headers=_auth(b.jwt),
            json={"user_note": "compromised by B"},
        )
    # READ ROW BACK — Supabase silent-200 trap on RLS-blocked PATCH
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT user_note FROM content.workspace_zettels WHERE id = $1", wz
            )
    note = asyncio.get_event_loop().run_until_complete(_check())
    assert note != "compromised by B", (
        f"User B's PATCH must NOT have modified user A's zettel "
        f"(http={resp.status_code}, user_note={note!r})"
    )


def test_kasten_get_cross_tenant_denied(v2_app, mint_user, asyncpg_pool):
    """User B from a different workspace cannot GET A's Kasten by id."""
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    kid = asyncio.get_event_loop().run_until_complete(
        _seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0])
    )
    with TestClient(v2_app) as client:
        resp = client.get(f"/api/rag/sandboxes/{kid}", headers=_auth(b.jwt))
    if resp.status_code == 503 and ("runtime" in resp.text.lower() or "not configured" in resp.text.lower()):
        pytest.skip("RAG/Sandbox runtime unavailable in test env")
    assert resp.status_code in (403, 404), resp.text


def test_kasten_adhoc_with_other_tenants_kasten_id_denied(v2_app, mint_user, asyncpg_pool):
    """B's POST /api/rag/adhoc with kasten_id pointing at A's Kasten must not leak."""
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    kid = asyncio.get_event_loop().run_until_complete(
        _seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0])
    )
    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/adhoc",
            headers=_auth(b.jwt),
            json={"content": "leak A's kasten", "sandbox_id": str(kid)},
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")
    # 200 is acceptable IF no A-data leaks (B's adhoc against B's own scope).
    # 402/403/404 are explicit denials.
    assert resp.status_code in (200, 402, 403, 404), resp.text
    if resp.status_code == 200:
        # No A-leak: A's email pattern must not appear
        body_text = resp.text
        assert a.email not in body_text, "user A's email leaked"
        # OWASP API1:2023 BOLA — UUID canaries catch leaks without email field.
        assert str(a.auth_user_id) not in body_text, "cross-tenant leak: A's auth_user_id in B's response"
        assert str(a.profile_id) not in body_text, "cross-tenant leak: A's profile_id in B's response"
        for ws_id in a.workspace_ids:
            assert str(ws_id) not in body_text, f"cross-tenant leak: A's workspace_id {ws_id} in B's response"


def test_kasten_members_list_cross_tenant_denied(v2_app, mint_user, asyncpg_pool):
    """B cannot list members of A's Kasten via the share endpoint."""
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    kid = asyncio.get_event_loop().run_until_complete(
        _seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0])
    )
    with TestClient(v2_app) as client:
        # /api/rag/sandboxes/{id}/members is the list-members endpoint
        # (also used to bulk-add via POST). GET should 404 for non-members.
        resp = client.get(f"/api/rag/sandboxes/{kid}/members", headers=_auth(b.jwt))
    if resp.status_code == 503 and ("runtime" in resp.text.lower() or "not configured" in resp.text.lower()):
        pytest.skip("RAG/Sandbox runtime unavailable in test env")
    # Some routers may not expose GET on this path — 404/403/405 all
    # acceptable as denial. The safety property is "no list of A's members".
    assert resp.status_code in (403, 404, 405), resp.text


def test_zettel_delete_cross_tenant_denied(v2_app, mint_user, asyncpg_pool):
    import asyncio
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    wz = asyncio.get_event_loop().run_until_complete(
        _seed_workspace_zettel(asyncpg_pool, workspace_id=a.workspace_ids[0])
    )
    with TestClient(v2_app) as client:
        resp = client.delete(f"/api/zettels/{wz}", headers=_auth(b.jwt))
    # READ ROW BACK — must still exist (soft-deleted is also a fail; A's row owns its own deletion)
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, deleted_at FROM content.workspace_zettels WHERE id = $1", wz
            )
            return row
    row = asyncio.get_event_loop().run_until_complete(_check())
    assert row is not None, (
        f"User B's DELETE must NOT have removed user A's zettel "
        f"(http={resp.status_code})"
    )
    assert row["deleted_at"] is None, (
        f"User B's DELETE must NOT have soft-deleted user A's zettel "
        f"(http={resp.status_code}, deleted_at={row['deleted_at']!r})"
    )
