"""Phase 4.1 v2 dual-path test for ``GET /api/graph``.

Drives the real FastAPI test client with DB v2 forced on, mints a fresh user
+ workspace, optionally seeds a canonical zettel + workspace overlay row, and
asserts the v2 path returns a non-500 ``KGGraph``-shaped JSON payload that
matches the v1 wire contract (``nodes`` + ``links`` arrays).

Marked ``@pytest.mark.live`` because it touches the live v2 Supabase project
and the FastAPI app's startup wiring.
"""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def v2_app(monkeypatch):
    """Build a fresh FastAPI app with DB v2 forced on for the test.

    The app is constructed AFTER ``DB_SCHEMA_VERSION=v2`` is set so any
    module-level branches read the v2 routing path. Module caches in
    ``website.core.persist`` and :mod:`website.api.auth` (JWKS) are reset
    between tests so each test sees a fresh CoreRepository / ContentRepository
    and a fresh JWKS client wired against the v2 auth endpoint.
    """
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    # Reset auth and persist caches so the new env is read.
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None

    from website.app import create_app

    app = create_app()
    return app


async def _seed_minimal_zettel(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one canonical_zettel + workspace_zettel via service-role asyncpg."""
    cz_id = uuid.uuid4()
    wz_id = uuid.uuid4()
    norm_url = f"https://api-graph-v2-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, 'web', $4, $5, '2026-04-01'::date)
            """,
            cz_id, norm_url, chash, "API graph v2 e2e zettel", "body",
        )
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, ai_summary,
                 user_tags, added_via)
            VALUES ($1, $2, $3, $4, $5, 'website')
            """,
            wz_id, workspace_id, cz_id,
            '{"brief_summary": "v2 graph e2e", "detailed_summary": "v2 graph e2e detail"}',
            ["v2", "graph"],
        )
    return cz_id, wz_id


def _auth_headers(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


@pytest.mark.asyncio
async def test_api_graph_v2_empty_workspace_returns_kggraph(
    v2_app, mint_user, asyncpg_pool
):
    """A user with a workspace and zero zettels should not 500 — v2 path
    returns a valid KGGraph (possibly empty). We assert the wire shape and a
    successful 200; the v1 path must stay UNCHANGED for callers without a v2
    scope, but here the user IS in v2."""
    user = mint_user(workspace_count=1)

    with TestClient(v2_app) as client:
        resp = client.get(
            "/api/graph?view=my", headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, (
            f"v2 /api/graph 5xx: status={resp.status_code} body={resp.text[:400]}"
        )
        body = resp.json()
        # v1 wire contract: top-level nodes + links arrays.
        assert isinstance(body, dict), f"expected JSON object, got {type(body)!r}"
        assert "nodes" in body and "links" in body, (
            f"missing nodes/links keys; got keys={list(body.keys())!r}"
        )
        assert isinstance(body["nodes"], list)
        assert isinstance(body["links"], list)


@pytest.mark.asyncio
async def test_api_graph_v2_seeded_zettel_appears_in_nodes(
    v2_app, mint_user, asyncpg_pool
):
    """After seeding one canonical zettel + workspace overlay, the v2 path
    must surface it as a graph node with the canonical title."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    cz_id, wz_id = await _seed_minimal_zettel(asyncpg_pool, workspace_id=ws_id)

    with TestClient(v2_app) as client:
        resp = client.get(
            "/api/graph?view=my", headers=_auth_headers(user.jwt),
        )
        assert resp.status_code == 200, (
            f"v2 /api/graph 5xx: status={resp.status_code} body={resp.text[:400]}"
        )
        body = resp.json()
        assert isinstance(body.get("nodes"), list)
        titles = {str(n.get("name") or "") for n in body["nodes"]}
        assert "API graph v2 e2e zettel" in titles, (
            f"seeded zettel title missing from v2 graph; got titles={titles!r}"
        )
