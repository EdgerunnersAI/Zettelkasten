"""End-to-end live test for the v2 graph_score retrieval-signal-weight path.

Phase 2.3 of the website-features-v2 purge: confirms `_usage_weight_bonus`
(refactored to call `rag.search_signal_weights`) end-to-ends correctly when
the underlying RPC is the live v2 Supabase project.

Service-role asyncpg seeds `rag.retrieval_signal_weights` rows for the user's
workspace, then exercises the public `_usage_weight_bonus` helper with a
user-scoped supabase-py client (so RLS + JWT app_metadata.workspace_ids
actually mediate access). Cleanup relies on the auth-user CASCADE configured
in the v2 schema.
"""
from __future__ import annotations

import math
import uuid

import asyncpg
import pytest

from website.core.supabase_v2.client import get_v2_user_client
from website.core.supabase_v2.repositories.rag_repository import RAGRepository
from website.features.rag_pipeline.retrieval.graph_score import _usage_weight_bonus
from website.features.rag_pipeline.types import QueryClass


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _embedding_literal(seed: float = 0.0) -> str:
    base = 0.001 + seed
    vals = [round(base + i * 1e-5, 6) for i in range(768)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


async def _seed_canonical_chunk(
    pool: asyncpg.Pool, *, embedding_seed: float
) -> uuid.UUID:
    """Insert a canonical_zettel + one canonical_chunk; return the chunk id."""
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
            cz_id, norm_url, content_hash, f"signal-w-z-{cz_id}", "body",
        )
        cc_id = uuid.uuid4()
        chunk_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
        emb = _embedding_literal(embedding_seed)
        await conn.execute(
            f"""
            INSERT INTO content.canonical_chunks
                (id, canonical_zettel_id, chunk_idx, content, content_hash,
                 chunk_type, embedding)
            VALUES ($1, $2, 0, $3, $4, 'atomic', '{emb}'::halfvec(768))
            """,
            cc_id, cz_id, "signal-w chunk 0", chunk_hash,
        )
        return cc_id


async def _seed_signal_weight(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    source_chunk_id: uuid.UUID,
    target_chunk_id: uuid.UUID,
    query_class: str,
    weight: float,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag.retrieval_signal_weights
                (workspace_id, source_canonical_chunk_id, target_canonical_chunk_id,
                 query_class, weight, refreshed_at)
            VALUES ($1, $2, $3, $4, $5, now())
            """,
            workspace_id, source_chunk_id, target_chunk_id, query_class, weight,
        )


def _expected_bonus(total_weight: float) -> float:
    return 0.10 / (1.0 + math.exp(-total_weight / 5.0)) - 0.05


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_score_signal_weight_bonus_matches_decay_formula(mint_user, asyncpg_pool):
    """Live v2 RPC `rag.search_signal_weights` reads seeded weights and
    `_usage_weight_bonus` returns the byte-identical sigmoid-bounded value:
    ``0.10 / (1 + exp(-sum_w / 5.0)) - 0.05``.
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    # Three canonical chunks: two sources + one shared target.
    src_a = await _seed_canonical_chunk(asyncpg_pool, embedding_seed=0.10)
    src_b = await _seed_canonical_chunk(asyncpg_pool, embedding_seed=0.20)
    target = await _seed_canonical_chunk(asyncpg_pool, embedding_seed=0.30)

    # Seed two weights for the same (target, query_class) pair → summed.
    await _seed_signal_weight(
        asyncpg_pool,
        workspace_id=ws_id,
        source_chunk_id=src_a,
        target_chunk_id=target,
        query_class="multi_hop",
        weight=10.0,
    )
    await _seed_signal_weight(
        asyncpg_pool,
        workspace_id=ws_id,
        source_chunk_id=src_b,
        target_chunk_id=target,
        query_class="multi_hop",
        weight=8.0,
    )
    # And a row in a different query_class to prove filtering works.
    await _seed_signal_weight(
        asyncpg_pool,
        workspace_id=ws_id,
        source_chunk_id=src_a,
        target_chunk_id=target,
        query_class="lookup",
        weight=42.0,
    )

    client = get_v2_user_client(user.jwt)
    repo = RAGRepository(client)

    # Multi-hop: weights 10 + 8 = 18 → expected sigmoid bonus.
    bonus_mh = _usage_weight_bonus(
        repo,
        user_id=ws_id,
        target_node_id=str(target),
        query_class=QueryClass.MULTI_HOP,
    )
    assert bonus_mh == pytest.approx(_expected_bonus(18.0), abs=1e-9)

    # Lookup: only the 42.0 row matches (different query_class).
    bonus_lk = _usage_weight_bonus(
        repo,
        user_id=ws_id,
        target_node_id=str(target),
        query_class=QueryClass.LOOKUP,
    )
    assert bonus_lk == pytest.approx(_expected_bonus(42.0), abs=1e-9)


@pytest.mark.asyncio
async def test_graph_score_signal_weight_bonus_zero_when_no_rows(mint_user, asyncpg_pool):
    """No matching (workspace, target, query_class) rows → bonus == 0
    (sigmoid(0) - 0.05 == 0). Live RPC, identity-contract preserved.
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    target = await _seed_canonical_chunk(asyncpg_pool, embedding_seed=0.45)

    client = get_v2_user_client(user.jwt)
    repo = RAGRepository(client)

    bonus = _usage_weight_bonus(
        repo,
        user_id=ws_id,
        target_node_id=str(target),
        query_class=QueryClass.MULTI_HOP,
    )
    assert bonus == pytest.approx(0.0, abs=1e-12)


@pytest.mark.asyncio
async def test_graph_score_signal_weight_workspace_isolation(mint_user, asyncpg_pool):
    """RLS / RPC workspace authorisation: a user MUST NOT see another
    workspace's signal weights even if they pass the right target_chunk_id.
    Forbidden RPC raises PostgREST error → bonus degrades to 0.0.
    """
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    owner_ws = owner.workspace_ids[0]

    src = await _seed_canonical_chunk(asyncpg_pool, embedding_seed=0.55)
    target = await _seed_canonical_chunk(asyncpg_pool, embedding_seed=0.65)
    await _seed_signal_weight(
        asyncpg_pool,
        workspace_id=owner_ws,
        source_chunk_id=src,
        target_chunk_id=target,
        query_class="multi_hop",
        weight=99.0,
    )

    # Intruder's JWT lacks owner_ws in its workspace_ids — RPC should refuse.
    intruder_client = get_v2_user_client(intruder.jwt)
    intruder_repo = RAGRepository(intruder_client)

    bonus = _usage_weight_bonus(
        intruder_repo,
        user_id=owner_ws,  # passes another user's workspace_id deliberately
        target_node_id=str(target),
        query_class=QueryClass.MULTI_HOP,
    )
    # _usage_weight_bonus catches all exceptions and returns 0.0 — the RPC
    # `unauthorized` (42501) raises, helper short-circuits to 0.0.
    assert bonus == 0.0
