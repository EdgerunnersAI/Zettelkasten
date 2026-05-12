"""WAVE-B Phase 1a smoke test: exercise mint_kasten + bulk_insert_zettels once.

This is the verification gate for the two additive fixtures before the four
module subagents dispatch in parallel. Asserts:

  * ``mint_kasten`` returns a populated ``MintedKasten`` and the row actually
    lands in ``rag.kastens`` scoped to the owner's personal workspace.
  * ``bulk_insert_zettels`` returns N IDs and each one resolves to a real
    ``content.workspace_zettels`` row in the owner's workspace.

We use ``n=10`` (NOT 500) for the smoke run — the goal is correctness of the
two factories, not throughput. The actual WAVE-B module tests will use larger
N where they need to.
"""
from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_mint_kasten_smoke(mint_user, mint_kasten, asyncpg_pool):
    """mint_kasten POSTs through the real route and returns a usable row."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    kasten_name = f"smoke-{uuid.uuid4().hex[:8]}"

    kasten = mint_kasten(owner_user=user, name=kasten_name)

    assert isinstance(kasten.sandbox_id, uuid.UUID)
    assert kasten.name == kasten_name
    assert kasten.owner_user_sub == user.auth_user_id

    async with asyncpg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT workspace_id, name FROM rag.kastens WHERE id = $1",
            kasten.sandbox_id,
        )
    assert row is not None, "mint_kasten must create a real rag.kastens row"
    assert row["workspace_id"] == ws_id
    assert row["name"] == kasten_name


@pytest.mark.asyncio
async def test_bulk_insert_zettels_smoke(mint_user, bulk_insert_zettels, asyncpg_pool):
    """bulk_insert_zettels returns N IDs and the rows are queryable."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    wz_ids = await bulk_insert_zettels(owner_user=user, n=10, prefix="smoke")

    assert len(wz_ids) == 10
    assert len(set(wz_ids)) == 10, "IDs must be unique"
    assert all(isinstance(wz_id, uuid.UUID) for wz_id in wz_ids)

    async with asyncpg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, workspace_id FROM content.workspace_zettels
             WHERE id = ANY($1::uuid[])
            """,
            wz_ids,
        )
    assert len(rows) == 10, "all 10 rows must be persisted"
    for r in rows:
        assert r["workspace_id"] == ws_id


@pytest.mark.asyncio
async def test_combined_smoke(mint_user, mint_kasten, bulk_insert_zettels, asyncpg_pool):
    """End-to-end: mint user, mint kasten, bulk-insert zettels — all in one
    test as the WAVE-B Phase 1a brief requires."""
    user = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=user)
    wz_ids = await bulk_insert_zettels(owner_user=user, n=10, prefix="combo")

    assert kasten.owner_user_sub == user.auth_user_id
    assert len(wz_ids) == 10

    async with asyncpg_pool.acquire() as conn:
        kasten_count = await conn.fetchval(
            "SELECT count(*) FROM rag.kastens WHERE id = $1",
            kasten.sandbox_id,
        )
        zettel_count = await conn.fetchval(
            "SELECT count(*) FROM content.workspace_zettels "
            "WHERE id = ANY($1::uuid[])",
            wz_ids,
        )
    assert kasten_count == 1
    assert zettel_count == 10
