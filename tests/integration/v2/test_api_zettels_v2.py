"""Phase 4.3 v2 dual-path tests for ``DELETE`` + ``PATCH /api/zettels/{id}``.

Marked ``@pytest.mark.live`` because the tests:

* Boot a real FastAPI app with ``DB_SCHEMA_VERSION=v2``.
* Mint a fresh Supabase Auth user + workspace via the service-role asyncpg
  fixture.
* Seed a real ``content.canonical_zettels`` + ``content.workspace_zettels``
  pair, then exercise the API with the user's JWT.
* Assert v2 behaviour: soft-delete (``deleted_at`` populated, canonical row
  preserved), partial update of ``user_tags`` / ``user_note`` / ``pinned``,
  and the ``ai_summary -> user_note`` redirect rule.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def v2_app(monkeypatch):
    """Build a fresh FastAPI app with DB v2 forced on for the test."""
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


async def _seed_minimal_zettel(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one canonical_zettel + workspace_zettel via service-role asyncpg.

    Returns (canonical_zettel_id, workspace_zettel_id).
    """
    cz_id = uuid.uuid4()
    wz_id = uuid.uuid4()
    norm_url = f"https://api-zettels-v2-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, 'web', $4, $5, '2026-04-01'::date)
            """,
            cz_id, norm_url, chash, "API zettels v2 e2e zettel", "body",
        )
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, ai_summary,
                 user_tags, user_note, pinned, added_via)
            VALUES ($1, $2, $3, $4, $5, NULL, false, 'website')
            """,
            wz_id, workspace_id, cz_id,
            '{"brief_summary": "v2 zettels e2e", "detailed_summary": "v2 zettels e2e detail"}',
            ["v2", "zettels"],
        )
    return cz_id, wz_id


@pytest.mark.asyncio
async def test_api_zettels_v2_soft_delete_sets_deleted_at_preserves_canonical(
    v2_app, mint_user, asyncpg_pool
):
    """DELETE on the v2 path soft-deletes the workspace overlay (sets
    ``deleted_at``) and leaves ``canonical_zettels`` intact — the reaper
    trigger handles canonical shred at last reference, not this handler."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    cz_id, wz_id = await _seed_minimal_zettel(asyncpg_pool, workspace_id=ws_id)

    with TestClient(v2_app) as client:
        resp = client.delete(
            f"/api/zettels/{wz_id}", headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, (
            f"v2 DELETE 5xx: status={resp.status_code} body={resp.text[:400]}"
        )
        assert resp.json()["status"] == "ok"

    async with asyncpg_pool.acquire() as conn:
        deleted_at = await conn.fetchval(
            "SELECT deleted_at FROM content.workspace_zettels WHERE id = $1",
            wz_id,
        )
        canonical_exists = await conn.fetchval(
            "SELECT 1 FROM content.canonical_zettels WHERE id = $1",
            cz_id,
        )

    assert deleted_at is not None, "soft-delete must populate deleted_at"
    assert canonical_exists == 1, (
        "canonical_zettels row must remain — reaper handles shred"
    )


@pytest.mark.asyncio
async def test_api_zettels_v2_patch_updates_user_fields_preserves_ai_summary(
    v2_app, mint_user, asyncpg_pool
):
    """PATCH on the v2 path updates ``user_tags`` + ``user_note`` and leaves
    the engine-owned ``ai_summary`` untouched."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    _cz_id, wz_id = await _seed_minimal_zettel(asyncpg_pool, workspace_id=ws_id)

    new_tags = ["alpha", "beta", "gamma"]
    new_note = "user-authored note text"

    with TestClient(v2_app) as client:
        resp = client.patch(
            f"/api/zettels/{wz_id}",
            json={"user_tags": new_tags, "user_note": new_note},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, (
            f"v2 PATCH 5xx: status={resp.status_code} body={resp.text[:400]}"
        )
        assert resp.json()["status"] == "ok"

    async with asyncpg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_tags, user_note, ai_summary, pinned
              FROM content.workspace_zettels
             WHERE id = $1
            """,
            wz_id,
        )

    assert list(row["user_tags"]) == new_tags
    assert row["user_note"] == new_note
    assert "v2 zettels e2e" in (row["ai_summary"] or ""), (
        "ai_summary must be unchanged by PATCH"
    )
    assert row["pinned"] is False, "pinned must remain default when not in payload"


@pytest.mark.asyncio
async def test_api_zettels_v2_patch_pin_toggle(
    v2_app, mint_user, asyncpg_pool
):
    """PATCH ``pinned=true`` then ``pinned=false`` toggles the column."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    _cz_id, wz_id = await _seed_minimal_zettel(asyncpg_pool, workspace_id=ws_id)

    with TestClient(v2_app) as client:
        resp = client.patch(
            f"/api/zettels/{wz_id}",
            json={"pinned": True},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]

        async with asyncpg_pool.acquire() as conn:
            pinned = await conn.fetchval(
                "SELECT pinned FROM content.workspace_zettels WHERE id = $1",
                wz_id,
            )
        assert pinned is True

        resp = client.patch(
            f"/api/zettels/{wz_id}",
            json={"pinned": False},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]

        async with asyncpg_pool.acquire() as conn:
            pinned = await conn.fetchval(
                "SELECT pinned FROM content.workspace_zettels WHERE id = $1",
                wz_id,
            )
        assert pinned is False


@pytest.mark.asyncio
async def test_api_zettels_v2_patch_ai_summary_routes_to_user_note(
    v2_app, mint_user, asyncpg_pool
):
    """A PATCH carrying ``ai_summary`` (legacy frontend) must NOT overwrite
    the engine-owned ``ai_summary`` column — the text is rerouted into
    ``user_note`` instead."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    _cz_id, wz_id = await _seed_minimal_zettel(asyncpg_pool, workspace_id=ws_id)

    sneaky_text = "client tried to overwrite ai_summary"

    with TestClient(v2_app) as client:
        resp = client.patch(
            f"/api/zettels/{wz_id}",
            json={"ai_summary": sneaky_text},
            headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]

    async with asyncpg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ai_summary, user_note
              FROM content.workspace_zettels
             WHERE id = $1
            """,
            wz_id,
        )

    assert row["user_note"] == sneaky_text, (
        "ai_summary payload must be rerouted into user_note"
    )
    assert "v2 zettels e2e" in (row["ai_summary"] or ""), (
        "ai_summary column must remain engine-owned and unchanged"
    )
