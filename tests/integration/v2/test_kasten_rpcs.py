"""Integration tests for the v2 kasten retrieval RPCs.

Covers the 5 RPCs shipped in `_v2/13_v2_kasten_rpcs.sql`:
  - rag.search_signal_weights
  - rag.chunk_share_for_kasten
  - rag.bulk_add_to_kasten
  - rag.fetch_anchor_seeds_v2
  - rag.list_kasten_zettels

For each RPC: a success path (caller authorised via JWT app_metadata.workspace_ids)
plus an authorisation-denial path (caller in a *different* workspace -> SQLSTATE 42501).
The denial path uses a user-scoped supabase-py client so RLS + the JWT-derived
`core.jwt_workspace_ids()` mediate access. The seed path uses the asyncpg pool
(service-role connection) because the supabase-py client cannot bind halfvec(768)
literals and writes to content.canonical_chunks need the RLS bypass.

All tests marked @pytest.mark.live — they hit the live v2 Supabase project.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest
from postgrest.exceptions import APIError

from website.core.supabase_v2.client import get_v2_user_client


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _embedding_literal(seed: float = 0.0) -> str:
    """Return a deterministic 768-dim halfvec literal '[v0,v1,...,v767]'.

    The values are tiny offsets so distinct seeds yield distinct vectors but
    every vector remains close to a unit-ish reference for stable cosine.
    """
    base = 0.001 + seed
    vals = [round(base + i * 1e-5, 6) for i in range(768)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


async def _seed_canonical_zettel_with_chunks(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    n_chunks: int = 2,
    embedding_seed: float = 0.0,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Insert a canonical_zettel + n_chunks canonical_chunks + workspace_zettel
    + workspace_chunk_membership rows using the service-role asyncpg pool.

    Returns (canonical_zettel_id, workspace_zettel_id, [canonical_chunk_id,...]).
    """
    async with pool.acquire() as conn:
        cz_id = uuid.uuid4()
        norm_url = f"https://example.test/{uuid.uuid4().hex}"
        # content_hash bytea — 32 random bytes is fine for tests
        content_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title, body_md)
            VALUES ($1, $2, $3, 'web', $4, $5)
            """,
            cz_id, norm_url, content_hash, f"test-zettel-{cz_id}", "body",
        )

        chunk_ids: list[uuid.UUID] = []
        for i in range(n_chunks):
            cc_id = uuid.uuid4()
            chunk_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
            emb_literal = _embedding_literal(embedding_seed + i * 0.01)
            # Cast halfvec via SQL literal — asyncpg can't bind halfvec natively.
            await conn.execute(
                f"""
                INSERT INTO content.canonical_chunks
                    (id, canonical_zettel_id, chunk_idx, content, content_hash,
                     chunk_type, embedding)
                VALUES ($1, $2, $3, $4, $5, 'atomic', '{emb_literal}'::halfvec(768))
                """,
                cc_id, cz_id, i, f"chunk content {i}", chunk_hash,
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
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID, name: str | None = None
) -> uuid.UUID:
    name = name or f"k-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO rag.kastens (workspace_id, name)
            VALUES ($1, $2)
            RETURNING id
            """,
            workspace_id, name,
        )
        return row["id"]


def _is_unauthorized(exc: BaseException) -> bool:
    """Match SQLSTATE 42501 or the literal 'unauthorized' message."""
    msg = str(exc).lower()
    code = getattr(exc, "code", None)
    return "unauthorized" in msg or "42501" in msg or code == "42501" or code == "P0001"


# ---------------------------------------------------------------------------
# 1. rag.search_signal_weights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_signal_weights_success(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    # Seed two chunks in the workspace and one signal-weight row pointing
    # source -> target inside the workspace.
    _, _, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=2
    )
    src, tgt = chunks[0], chunks[1]
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag.retrieval_signal_weights
                (workspace_id, source_canonical_chunk_id, target_canonical_chunk_id,
                 query_class, weight)
            VALUES ($1, $2, $3, 'factoid', 0.42)
            """,
            ws_id, src, tgt,
        )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "search_signal_weights",
        {
            "p_workspace_id": str(ws_id),
            "p_target_chunk_ids": [str(tgt)],
            "p_query_class": "factoid",
        },
    ).execute()
    rows = resp.data or []
    assert len(rows) == 1, f"expected 1 row, got {rows!r}"
    row = rows[0]
    assert uuid.UUID(row["source_canonical_chunk_id"]) == src
    assert uuid.UUID(row["target_canonical_chunk_id"]) == tgt
    assert row["weight"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_search_signal_weights_authz_denial(mint_user, asyncpg_pool):
    # Two users with disjoint workspaces.
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    assert ws_id not in intruder.workspace_ids

    _, _, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )
    client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("rag").rpc(
            "search_signal_weights",
            {
                "p_workspace_id": str(ws_id),
                "p_target_chunk_ids": [str(chunks[0])],
                "p_query_class": "factoid",
            },
        ).execute()
    assert _is_unauthorized(exc_info.value), f"expected unauthorized, got {exc_info.value!r}"


# ---------------------------------------------------------------------------
# 2. rag.chunk_share_for_kasten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_share_for_kasten_success(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    _, wz_id, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=2
    )
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
            VALUES ($1, $2, 'manual')
            """,
            kasten_id, wz_id,
        )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "chunk_share_for_kasten", {"p_kasten_id": str(kasten_id)}
    ).execute()
    rows = resp.data or []
    by_id = {uuid.UUID(r["canonical_chunk_id"]): r["chunk_count"] for r in rows}
    for cc_id in chunks:
        assert by_id.get(cc_id) == 1, f"expected count 1 for {cc_id}, got {by_id!r}"


@pytest.mark.asyncio
async def test_chunk_share_for_kasten_authz_denial(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)

    client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("rag").rpc(
            "chunk_share_for_kasten", {"p_kasten_id": str(kasten_id)}
        ).execute()
    assert _is_unauthorized(exc_info.value), f"expected unauthorized, got {exc_info.value!r}"


# ---------------------------------------------------------------------------
# 3. rag.bulk_add_to_kasten (+ idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_add_to_kasten_success_and_idempotent(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    _, wz_id, _ = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )
    _, wz2_id, _ = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1, embedding_seed=0.5
    )
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)

    client = get_v2_user_client(user.jwt)
    resp1 = client.schema("rag").rpc(
        "bulk_add_to_kasten",
        {"p_kasten_id": str(kasten_id), "p_workspace_zettel_ids": [str(wz_id), str(wz2_id)]},
    ).execute()
    assert resp1.data == 2, f"expected 2 inserts on first call, got {resp1.data!r}"

    # Idempotency: second call with same args returns 0 (ON CONFLICT DO NOTHING).
    resp2 = client.schema("rag").rpc(
        "bulk_add_to_kasten",
        {"p_kasten_id": str(kasten_id), "p_workspace_zettel_ids": [str(wz_id), str(wz2_id)]},
    ).execute()
    assert resp2.data == 0, f"expected 0 inserts on second call, got {resp2.data!r}"

    # Membership rows actually exist with added_via='bulk_rpc'.
    async with asyncpg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT workspace_zettel_id, added_via FROM rag.kasten_zettels
             WHERE kasten_id = $1 ORDER BY added_at
            """,
            kasten_id,
        )
        assert len(rows) == 2
        assert all(r["added_via"] == "bulk_rpc" for r in rows)


@pytest.mark.asyncio
async def test_bulk_add_to_kasten_authz_denial(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    _, wz_id, _ = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)

    client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("rag").rpc(
            "bulk_add_to_kasten",
            {"p_kasten_id": str(kasten_id), "p_workspace_zettel_ids": [str(wz_id)]},
        ).execute()
    assert _is_unauthorized(exc_info.value), f"expected unauthorized, got {exc_info.value!r}"


# ---------------------------------------------------------------------------
# 4. rag.fetch_anchor_seeds_v2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_anchor_seeds_v2_success(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    _, wz_id, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=3
    )
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
            VALUES ($1, $2, 'manual')
            """,
            kasten_id, wz_id,
        )

    client = get_v2_user_client(user.jwt)
    query_emb = _embedding_literal(0.0)
    resp = client.schema("rag").rpc(
        "fetch_anchor_seeds_v2",
        {
            "p_kasten_id": str(kasten_id),
            "p_anchor_canonical_chunk_ids": [str(c) for c in chunks],
            "p_query_embedding": query_emb,
        },
    ).execute()
    rows = resp.data or []
    # ROW_NUMBER() partitions by canonical_zettel_id and keeps rn=1 — one
    # zettel here, so exactly one row regardless of n_chunks.
    assert len(rows) == 1, f"expected 1 row (rn=1 per zettel), got {rows!r}"
    row = rows[0]
    assert uuid.UUID(row["canonical_chunk_id"]) in chunks
    assert 0.0 <= row["score"] <= 1.0001


@pytest.mark.asyncio
async def test_fetch_anchor_seeds_v2_authz_denial(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    _, _, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)

    client = get_v2_user_client(intruder.jwt)
    query_emb = _embedding_literal(0.0)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("rag").rpc(
            "fetch_anchor_seeds_v2",
            {
                "p_kasten_id": str(kasten_id),
                "p_anchor_canonical_chunk_ids": [str(chunks[0])],
                "p_query_embedding": query_emb,
            },
        ).execute()
    assert _is_unauthorized(exc_info.value), f"expected unauthorized, got {exc_info.value!r}"


# ---------------------------------------------------------------------------
# 5. rag.list_kasten_zettels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_kasten_zettels_success(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    cz_id, wz_id, _ = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
            VALUES ($1, $2, 'manual')
            """,
            kasten_id, wz_id,
        )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "list_kasten_zettels", {"p_kasten_id": str(kasten_id)}
    ).execute()
    rows = resp.data or []
    assert len(rows) == 1
    row = rows[0]
    assert uuid.UUID(row["workspace_zettel_id"]) == wz_id
    assert uuid.UUID(row["canonical_zettel_id"]) == cz_id
    assert row["source_type"] == "web"
    assert row["user_tags"] == []


@pytest.mark.asyncio
async def test_list_kasten_zettels_authz_denial(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)

    client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("rag").rpc(
            "list_kasten_zettels", {"p_kasten_id": str(kasten_id)}
        ).execute()
    assert _is_unauthorized(exc_info.value), f"expected unauthorized, got {exc_info.value!r}"
