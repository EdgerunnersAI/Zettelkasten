"""Phase 4.4 v2 dual-path tests for kasten CRUD via ``/api/rag/sandboxes``.

Marked ``@pytest.mark.live`` because the tests:

* Boot a real FastAPI app with ``DB_SCHEMA_VERSION=v2``.
* Mint a fresh Supabase Auth user + workspace via the service-role asyncpg
  fixture.
* Seed real ``content.canonical_zettels`` / ``content.workspace_zettels``
  rows, then exercise the API with the user's JWT.
* Assert v2 behaviour: rows actually land in ``rag.kastens`` /
  ``rag.kasten_zettels`` and cross-tenant access is denied.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def v2_app(monkeypatch):
    """Build a fresh FastAPI app with DB v2 forced on for the test.

    Pricing is monkey-patched out at the route-module level — Phase 4.4 is
    scoped to kasten CRUD on ``rag.kastens``; entitlement enforcement is
    canonical pricing-module behaviour and is exercised in its own test
    suite. Patching ``require_entitlement``/``consume_entitlement`` lets the
    test exercise the dual-path without seeding pricing rows (forbidden by
    the pricing-module-authority rule in CLAUDE.md).
    """
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None

    async def _noop(*_args, **_kwargs):  # noqa: D401
        return None

    from website.api import sandbox_routes as sandbox_routes_mod
    monkeypatch.setattr(sandbox_routes_mod, "require_entitlement", _noop)
    monkeypatch.setattr(sandbox_routes_mod, "consume_entitlement", _noop)

    from website.app import create_app

    return create_app()


def _auth_headers(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _seed_workspace_zettel(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID
) -> uuid.UUID:
    """Insert one canonical_zettel + workspace_zettel via service-role asyncpg.

    Returns the workspace_zettel_id (this is what the kasten add-zettels API
    consumes as ``node_ids`` in the v2 dual-path).
    """
    cz_id = uuid.uuid4()
    wz_id = uuid.uuid4()
    norm_url = f"https://sandbox-routes-v2-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, 'web', $4, $5, '2026-04-01'::date)
            """,
            cz_id, norm_url, chash, "sandbox routes v2 e2e zettel", "body",
        )
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, ai_summary,
                 user_tags, user_note, pinned, added_via)
            VALUES ($1, $2, $3, $4, $5, NULL, false, 'website')
            """,
            wz_id, workspace_id, cz_id,
            '{"brief_summary": "v2 sandbox routes e2e", "detailed_summary": "v2 sandbox routes e2e detail"}',
            ["v2", "sandbox"],
        )
    return wz_id


@pytest.mark.asyncio
async def test_sandbox_routes_v2_create_then_list(v2_app, mint_user, asyncpg_pool):
    """POST creates a kasten in ``rag.kastens``; GET list returns it
    scoped to the user's workspace."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    name = f"k-{uuid.uuid4().hex[:8]}"

    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/sandboxes",
            json={"name": name, "default_quality": "fast"},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, (
            f"v2 POST 5xx: status={resp.status_code} body={resp.text[:400]}"
        )
        kasten_id = resp.json()["sandbox"]["id"]

        # Row actually landed in rag.kastens scoped to the user's workspace.
        async with asyncpg_pool.acquire() as conn:
            db_row = await conn.fetchrow(
                "SELECT id, workspace_id, name FROM rag.kastens WHERE id = $1",
                uuid.UUID(kasten_id),
            )
        assert db_row is not None, "kasten row must exist in rag.kastens"
        assert db_row["workspace_id"] == ws_id
        assert db_row["name"] == name

        resp = client.get("/api/rag/sandboxes", headers=_auth_headers(user.jwt))
        assert resp.status_code == 200, resp.text[:400]
        ids = [s["id"] for s in resp.json()["sandboxes"]]
        assert kasten_id in ids


@pytest.mark.asyncio
async def test_sandbox_routes_v2_add_zettels_then_list(
    v2_app, mint_user, asyncpg_pool
):
    """POST add-zettels routes through ``rag.bulk_add_to_kasten``; the
    list-members endpoint surfaces the joined zettel rows via
    ``rag.list_kasten_zettels``."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    wz1 = await _seed_workspace_zettel(asyncpg_pool, workspace_id=ws_id)
    wz2 = await _seed_workspace_zettel(asyncpg_pool, workspace_id=ws_id)

    with TestClient(v2_app) as client:
        # Create the kasten via the API (also exercises the dual-path POST).
        resp = client.post(
            "/api/rag/sandboxes",
            json={"name": f"k-{uuid.uuid4().hex[:8]}"},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]
        kasten_id = resp.json()["sandbox"]["id"]

        resp = client.post(
            f"/api/rag/sandboxes/{kasten_id}/members",
            json={"node_ids": [str(wz1), str(wz2)], "added_via": "manual"},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, (
            f"v2 add zettels 5xx: status={resp.status_code} body={resp.text[:400]}"
        )
        body = resp.json()
        assert body["added_count"] == 2

        # Memberships actually exist in rag.kasten_zettels.
        async with asyncpg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT workspace_zettel_id FROM rag.kasten_zettels
                 WHERE kasten_id = $1
                """,
                uuid.UUID(kasten_id),
            )
        member_ids = {str(r["workspace_zettel_id"]) for r in rows}
        assert member_ids == {str(wz1), str(wz2)}

        # GET list-members returns both zettels via the RPC join.
        resp = client.get(
            f"/api/rag/sandboxes/{kasten_id}/members",
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]
        node_ids = {m["node_id"] for m in resp.json()["members"]}
        assert node_ids == {str(wz1), str(wz2)}


@pytest.mark.asyncio
async def test_sandbox_routes_v2_delete_kasten(v2_app, mint_user, asyncpg_pool):
    """DELETE removes the row from ``rag.kastens``; subsequent GET list does
    not include it."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/sandboxes",
            json={"name": f"k-{uuid.uuid4().hex[:8]}"},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]
        kasten_id = resp.json()["sandbox"]["id"]

        resp = client.delete(
            f"/api/rag/sandboxes/{kasten_id}",
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]

        async with asyncpg_pool.acquire() as conn:
            still_there = await conn.fetchval(
                "SELECT 1 FROM rag.kastens WHERE id = $1 AND workspace_id = $2",
                uuid.UUID(kasten_id), ws_id,
            )
        assert still_there is None, "kasten row must be deleted from rag.kastens"

        resp = client.get("/api/rag/sandboxes", headers=_auth_headers(user.jwt))
        assert resp.status_code == 200, resp.text[:400]
        assert kasten_id not in [s["id"] for s in resp.json()["sandboxes"]]


@pytest.mark.asyncio
async def test_sandbox_routes_v2_cross_tenant_denial(
    v2_app, mint_user, asyncpg_pool
):
    """User B's JWT MUST NOT see User A's kastens via list, nor be able to
    delete A's kasten. The repository's workspace_id eq-filter (and the
    underlying RLS on ``rag.kastens``) are the gates under test."""
    user_a = mint_user(workspace_count=1)
    user_b = mint_user(workspace_count=1)

    with TestClient(v2_app) as client:
        # A creates a kasten.
        resp = client.post(
            "/api/rag/sandboxes",
            json={"name": f"a-{uuid.uuid4().hex[:8]}"},
            headers=_auth_headers(user_a.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]
        a_kasten_id = resp.json()["sandbox"]["id"]

        # B lists their own kastens — must NOT include A's.
        resp = client.get(
            "/api/rag/sandboxes", headers=_auth_headers(user_b.jwt)
        )
        assert resp.status_code == 200, resp.text[:400]
        b_visible_ids = [s["id"] for s in resp.json()["sandboxes"]]
        assert a_kasten_id not in b_visible_ids, (
            "User B must NOT see User A's kasten in list"
        )

        # B attempts to delete A's kasten — must be 404 (not found in B's
        # workspace scope) and the row must remain intact.
        resp = client.delete(
            f"/api/rag/sandboxes/{a_kasten_id}",
            headers=_auth_headers(user_b.jwt),
        )
        assert resp.status_code == 404, (
            f"cross-tenant delete must 404, got {resp.status_code}: {resp.text[:400]}"
        )

    async with asyncpg_pool.acquire() as conn:
        still_there = await conn.fetchval(
            "SELECT 1 FROM rag.kastens WHERE id = $1",
            uuid.UUID(a_kasten_id),
        )
    assert still_there == 1, "User A's kasten must remain after B's failed delete"
