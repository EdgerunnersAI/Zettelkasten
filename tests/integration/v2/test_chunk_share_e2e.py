"""End-to-end live test for the v2 chunk-share retrieval path.

Phase 2.2 of the website-features-v2 purge: confirms `ChunkShareStore`
(refactored to call `rag.chunk_share_for_kasten`) end-to-ends correctly when
the underlying RPC is the live v2 Supabase project.

The seed-and-call shape mirrors `tests/integration/v2/test_kasten_rpcs.py`:
seed canonical_zettels + canonical_chunks + workspace_zettels + workspace_chunk_membership
+ rag.kasten_zettels rows via the service-role asyncpg pool, then exercise the
public `ChunkShareStore.get_chunk_counts` API with a user-scoped supabase-py
client (so RLS + JWT app_metadata.workspace_ids actually mediate access).

Cleanup relies on the auth-user CASCADE configured in the v2 schema: deleting
auth.users.id sweeps profile, workspace_zettels, kasten_zettels, etc. The
`mint_user` fixture handles that teardown.
"""
from __future__ import annotations

import math
import uuid

import asyncpg
import pytest

from website.core.supabase_v2.client import get_v2_user_client
from website.features.rag_pipeline.retrieval.chunk_share import (
    ChunkShareStore,
    compute_chunk_share_penalty,
)


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Seed helpers (mirrors test_kasten_rpcs.py to keep the contract identical).
# ---------------------------------------------------------------------------


def _embedding_literal(seed: float = 0.0) -> str:
    base = 0.001 + seed
    vals = [round(base + i * 1e-5, 6) for i in range(768)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


async def _seed_zettel_with_chunks(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    n_chunks: int,
    embedding_seed: float,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Insert canonical_zettel + n canonical_chunks + workspace_zettel +
    workspace_chunk_membership. Returns (cz_id, wz_id, [cc_ids]).
    """
    async with pool.acquire() as conn:
        cz_id = uuid.uuid4()
        norm_url = f"https://example.test/{uuid.uuid4().hex}"
        content_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title, body_md)
            VALUES ($1, $2, $3, 'web', $4, $5)
            """,
            cz_id, norm_url, content_hash, f"chunk-share-zettel-{cz_id}", "body",
        )

        chunk_ids: list[uuid.UUID] = []
        for i in range(n_chunks):
            cc_id = uuid.uuid4()
            chunk_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
            emb = _embedding_literal(embedding_seed + i * 0.01)
            await conn.execute(
                f"""
                INSERT INTO content.canonical_chunks
                    (id, canonical_zettel_id, chunk_idx, content, content_hash,
                     chunk_type, embedding)
                VALUES ($1, $2, $3, $4, $5, 'atomic', '{emb}'::halfvec(768))
                """,
                cc_id, cz_id, i, f"cs chunk {i}", chunk_hash,
            )
            chunk_ids.append(cc_id)

        wz_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, added_via)
            VALUES ($1, $2, $3, 'website')
            """,
            wz_id, workspace_id, cz_id,
        )
        for cc_id in chunk_ids:
            await conn.execute(
                """
                INSERT INTO content.workspace_chunk_membership
                    (workspace_id, canonical_chunk_id, workspace_zettel_id)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                """,
                workspace_id, cc_id, wz_id,
            )

        return cz_id, wz_id, chunk_ids


async def _create_kasten(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID
) -> uuid.UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO rag.kastens (workspace_id, name)
            VALUES ($1, $2)
            RETURNING id
            """,
            workspace_id, f"chunk-share-k-{uuid.uuid4().hex[:8]}",
        )
        return row["id"]


async def _link_zettel_to_kasten(
    pool: asyncpg.Pool, *, kasten_id: uuid.UUID, wz_id: uuid.UUID
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
            VALUES ($1, $2, 'manual')
            """,
            kasten_id, wz_id,
        )


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_share_e2e_returns_correct_counts(mint_user, asyncpg_pool):
    """Public `ChunkShareStore.get_chunk_counts` returns canonical_chunk_id counts
    via the live `rag.chunk_share_for_kasten` RPC.

    Seeds two zettels (3 chunks + 5 chunks) inside one Kasten. Each canonical
    chunk is membership-counted exactly once per (kasten_id, canonical_chunk_id)
    pair, so each row's chunk_count must be 1 (one workspace_zettel per chunk).
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    _, wz1, chunks_a = await _seed_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=3, embedding_seed=0.0,
    )
    _, wz2, chunks_b = await _seed_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=5, embedding_seed=0.5,
    )
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)
    await _link_zettel_to_kasten(asyncpg_pool, kasten_id=kasten_id, wz_id=wz1)
    await _link_zettel_to_kasten(asyncpg_pool, kasten_id=kasten_id, wz_id=wz2)

    # Build a v2 user-scoped client (RLS + JWT app_metadata.workspace_ids).
    client = get_v2_user_client(user.jwt)
    # Bypass the TTLCache by giving each test its own store instance.
    store = ChunkShareStore(supabase=client, ttl_seconds=60.0)

    counts = await store.get_chunk_counts(sandbox_id=kasten_id)

    expected_ids = {str(cc) for cc in (chunks_a + chunks_b)}
    assert set(counts.keys()) == expected_ids, (
        f"missing/extra chunk ids: got {set(counts.keys())!r}, expected {expected_ids!r}"
    )
    # Each canonical chunk is referenced by exactly one workspace_zettel here,
    # so chunk_count == 1 across the board (RPC GROUP BY canonical_chunk_id).
    for cc_id, n in counts.items():
        assert n == 1, f"expected count 1 for {cc_id}, got {n}"
        # Damping at chunk_count=1 is identity (1.0) — sanity-check the math
        # invariant the caller actually applies.
        assert compute_chunk_share_penalty(n) == 1.0


@pytest.mark.asyncio
async def test_chunk_share_e2e_damping_matches_inverse_sqrt(mint_user, asyncpg_pool):
    """When the same canonical chunk is reachable through N workspace_zettels
    inside one Kasten, the RPC reports chunk_count=N and the public damping
    factor matches 1/sqrt(N).

    Construction: 4 distinct canonical_zettels (workspace_zettels has a
    UNIQUE(workspace_id, canonical_zettel_id) constraint, so we cannot reuse
    one canonical_zettel). The first canonical_zettel owns one shared
    canonical_chunk (FK content.canonical_chunks.canonical_zettel_id). The
    other 3 canonical_zettels' workspace_zettels each get a
    workspace_chunk_membership row pointing to that SAME shared chunk
    (PK is (workspace_id, canonical_chunk_id, workspace_zettel_id), so
    multiple workspace_zettels can co-membership the same chunk). All 4
    workspace_zettels are then linked into the Kasten — the v2 RPC
    `rag.chunk_share_for_kasten` GROUP BYs canonical_chunk_id and counts the
    membership joins, so chunk_count=4.
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    # Seed canonical-zettel #1 with one canonical_chunk + its workspace_zettel
    # + workspace_chunk_membership row.
    _, wz_first, chunks = await _seed_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1, embedding_seed=0.7,
    )
    shared_cc_id = chunks[0]

    # Seed 3 *additional* canonical_zettels (each with its own workspace_zettel
    # but no chunks of their own), and then add workspace_chunk_membership
    # rows that map their workspace_zettels to the shared chunk above.
    extra_wz: list[uuid.UUID] = []
    async with asyncpg_pool.acquire() as conn:
        for i in range(3):
            cz_extra = uuid.uuid4()
            content_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
            await conn.execute(
                """
                INSERT INTO content.canonical_zettels
                    (id, normalized_url, content_hash, source_type, title, body_md)
                VALUES ($1, $2, $3, 'web', $4, $5)
                """,
                cz_extra,
                f"https://example.test/{uuid.uuid4().hex}",
                content_hash,
                f"chunk-share-extra-{i}",
                "body",
            )
            wz_extra = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO content.workspace_zettels
                    (id, workspace_id, canonical_zettel_id, added_via)
                VALUES ($1, $2, $3, 'website')
                """,
                wz_extra, ws_id, cz_extra,
            )
            # Add the membership row pointing this workspace_zettel at the
            # shared canonical_chunk. PK is (workspace_id, canonical_chunk_id,
            # workspace_zettel_id) so this is allowed.
            await conn.execute(
                """
                INSERT INTO content.workspace_chunk_membership
                    (workspace_id, canonical_chunk_id, workspace_zettel_id)
                VALUES ($1, $2, $3)
                """,
                ws_id, shared_cc_id, wz_extra,
            )
            extra_wz.append(wz_extra)

    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)
    await _link_zettel_to_kasten(asyncpg_pool, kasten_id=kasten_id, wz_id=wz_first)
    for wz_id in extra_wz:
        await _link_zettel_to_kasten(asyncpg_pool, kasten_id=kasten_id, wz_id=wz_id)

    client = get_v2_user_client(user.jwt)
    store = ChunkShareStore(supabase=client, ttl_seconds=60.0)
    counts = await store.get_chunk_counts(sandbox_id=kasten_id)

    # 1 original + 3 extra = 4 workspace_zettels mapping to this chunk,
    # all linked to the kasten → chunk_count must be 4.
    assert counts.get(str(shared_cc_id)) == 4, (
        f"expected count 4 for shared chunk, got {counts!r}"
    )
    damping = compute_chunk_share_penalty(counts[str(shared_cc_id)])
    assert damping == pytest.approx(1.0 / math.sqrt(4), abs=1e-6)
