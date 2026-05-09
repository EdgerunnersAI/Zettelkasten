"""Integration tests for the Phase 1.D v2 RPCs.

Covers the 5 RPCs shipped across `_v2/19_…` through `_v2/24_…`:

  - content.search_chunks_enriched      (19_enriched_search_rpc.sql)
  - content.hybrid_search_chunks        (20_hybrid_search_rpc.sql)
  - rag.resolve_effective_nodes_v2      (21_resolve_effective_nodes_rpc.sql)
  - kg.resolve_entity_anchors_v2        (23_resolve_entity_anchors_rpc.sql)
  - kg.entities_to_anchor_chunks        (24_entities_to_anchor_chunks_rpc.sql)

Plus the supporting `kg.kg_node_aliases` table from 22_kg_aliases_table.sql.

For each RPC:
  - One or more success paths (caller authorised via JWT app_metadata.workspace_ids).
  - An authz-denial path (caller in a different workspace -> SQLSTATE 42501).

Marked @pytest.mark.live — these hit the live v2 Supabase project.

Seeds use the asyncpg service-role pool because:
  * supabase-py / postgrest-py cannot bind halfvec(768) literals.
  * Inserts to content.canonical_chunks bypass RLS via service-role.
"""
from __future__ import annotations

import math
import uuid

import asyncpg
import pytest
from postgrest.exceptions import APIError

from website.core.supabase_v2.client import get_v2_user_client


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers (mirrors test_kasten_rpcs.py patterns)
# ---------------------------------------------------------------------------


def _embedding_literal(seed: float = 0.0) -> str:
    """Return a deterministic 768-dim halfvec literal '[v0,v1,...,v767]'."""
    base = 0.001 + seed
    vals = [round(base + i * 1e-5, 6) for i in range(768)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


async def _seed_canonical_zettel_with_chunks(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    n_chunks: int = 2,
    embedding_seed: float = 0.0,
    chunk_contents: list[str] | None = None,
    title: str | None = None,
    source_type: str = "web",
    user_tags: list[str] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Insert canonical_zettel + n_chunks canonical_chunks + workspace_zettel
    + workspace_chunk_membership rows using the service-role asyncpg pool.

    Returns (canonical_zettel_id, workspace_zettel_id, [canonical_chunk_id,...]).
    """
    if user_tags is None:
        user_tags = []
    if chunk_contents is None:
        chunk_contents = [f"chunk content {i}" for i in range(n_chunks)]
    assert len(chunk_contents) == n_chunks

    async with pool.acquire() as conn:
        cz_id = uuid.uuid4()
        norm_url = f"https://example.test/{uuid.uuid4().hex}"
        content_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title, body_md,
                 publication_date)
            VALUES ($1, $2, $3, $4, $5, $6, '2026-04-01'::date)
            """,
            cz_id, norm_url, content_hash, source_type,
            title or f"test-zettel-{cz_id}", "body",
        )

        chunk_ids: list[uuid.UUID] = []
        for i, body in enumerate(chunk_contents):
            cc_id = uuid.uuid4()
            chunk_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
            emb_literal = _embedding_literal(embedding_seed + i * 0.01)
            await conn.execute(
                f"""
                INSERT INTO content.canonical_chunks
                    (id, canonical_zettel_id, chunk_idx, content, content_hash,
                     chunk_type, embedding)
                VALUES ($1, $2, $3, $4, $5, 'atomic', '{emb_literal}'::halfvec(768))
                """,
                cc_id, cz_id, i, body, chunk_hash,
            )
            chunk_ids.append(cc_id)

        wz_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, added_via, user_tags)
            VALUES ($1, $2, $3, 'website', $4)
            """,
            wz_id, workspace_id, cz_id, user_tags,
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


# ===========================================================================
# 1. content.search_chunks_enriched (19_enriched_search_rpc.sql)
# ===========================================================================


@pytest.mark.asyncio
async def test_search_chunks_enriched_success(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    title = f"enriched-zettel-{uuid.uuid4().hex[:6]}"
    user_tags = ["machine-learning", "rag"]
    _, wz_id, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool,
        workspace_id=ws_id,
        n_chunks=2,
        title=title,
        source_type="github",
        user_tags=user_tags,
    )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("content").rpc(
        "search_chunks_enriched",
        {
            "p_workspace_id": str(ws_id),
            "p_query_embedding": _embedding_literal(0.0),
            "p_match_count": 5,
        },
    ).execute()
    rows = resp.data or []
    assert len(rows) == 2, f"expected 2 rows, got {rows!r}"

    # Verify enriched columns are populated for the first row.
    row = rows[0]
    assert uuid.UUID(row["canonical_chunk_id"]) in chunks
    assert row["title"] == title
    assert row["source_type"] == "github"
    assert row["publication_date"] == "2026-04-01"
    assert row["user_tags"] == user_tags
    assert uuid.UUID(row["workspace_zettel_id"]) == wz_id
    assert row["fts_text"] is not None
    assert 0.0 <= row["score"] <= 1.0001


@pytest.mark.asyncio
async def test_search_chunks_enriched_authz_denial(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    assert ws_id not in intruder.workspace_ids

    await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )

    client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("content").rpc(
            "search_chunks_enriched",
            {
                "p_workspace_id": str(ws_id),
                "p_query_embedding": _embedding_literal(0.0),
                "p_match_count": 5,
            },
        ).execute()
    assert _is_unauthorized(exc_info.value), \
        f"expected unauthorized, got {exc_info.value!r}"


@pytest.mark.asyncio
async def test_search_chunks_enriched_excludes_soft_deleted(
    mint_user, asyncpg_pool
):
    """Soft-deleted workspace_zettels (deleted_at IS NOT NULL) must NOT surface."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    _, wz_id, _ = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE content.workspace_zettels SET deleted_at = now() WHERE id = $1",
            wz_id,
        )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("content").rpc(
        "search_chunks_enriched",
        {
            "p_workspace_id": str(ws_id),
            "p_query_embedding": _embedding_literal(0.0),
            "p_match_count": 5,
        },
    ).execute()
    rows = resp.data or []
    assert rows == [], f"expected soft-deleted rows excluded, got {rows!r}"


# ===========================================================================
# 2. content.hybrid_search_chunks (20_hybrid_search_rpc.sql)
# ===========================================================================


@pytest.mark.asyncio
async def test_hybrid_search_dense_only(mint_user, asyncpg_pool):
    """Query text matches no chunk via FTS — only the semantic CTE contributes.

    All chunks have embeddings; rrf_score should equal 1/(k + semantic_rank).
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    # Seed 2 chunks with bland content; the query text will use a token that
    # cannot match any of them (FTS empty).
    _, _, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool,
        workspace_id=ws_id,
        n_chunks=2,
        chunk_contents=["alpha bravo charlie", "delta echo foxtrot"],
    )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("content").rpc(
        "hybrid_search_chunks",
        {
            "p_workspace_id": str(ws_id),
            "p_query_text": "zzqzzqzzq_no_match_token",
            "p_query_embedding": _embedding_literal(0.0),
            "p_match_count": 5,
            "p_rrf_k": 60,
            "p_full_text_weight": 1.0,
            "p_semantic_weight": 1.0,
        },
    ).execute()
    rows = resp.data or []
    assert len(rows) == 2, f"expected 2 dense-only rows, got {rows!r}"
    for row in rows:
        # FTS rank should be NULL because no FTS match.
        assert row["fts_rank"] is None
        assert row["semantic_rank"] in (1, 2)
        # rrf_score == 1/(60 + sem_rank)
        expected = 1.0 / (60 + row["semantic_rank"])
        assert math.isclose(row["rrf_score"], expected, rel_tol=1e-9), (
            f"rrf mismatch: got {row['rrf_score']}, expected {expected}"
        )


@pytest.mark.asyncio
async def test_hybrid_search_fts_only(mint_user, asyncpg_pool):
    """Chunks have NULL embeddings — only FTS CTE contributes.

    Insert chunks via service-role asyncpg directly (bypassing the helper)
    because the helper sets a real embedding; this case demands NULL.
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    async with asyncpg_pool.acquire() as conn:
        cz_id = uuid.uuid4()
        norm_url = f"https://example.test/{uuid.uuid4().hex}"
        ch = uuid.uuid4().bytes + uuid.uuid4().bytes
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title, body_md)
            VALUES ($1, $2, $3, 'web', 'fts-only', 'body')
            """,
            cz_id, norm_url, ch,
        )
        cc_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO content.canonical_chunks
                (id, canonical_zettel_id, chunk_idx, content, content_hash,
                 chunk_type, embedding)
            VALUES ($1, $2, 0,
                    'transformer attention mechanism is fascinating',
                    $3, 'atomic', NULL)
            """,
            cc_id, cz_id, uuid.uuid4().bytes + uuid.uuid4().bytes,
        )
        wz_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, added_via)
            VALUES ($1, $2, $3, 'website')
            """,
            wz_id, ws_id, cz_id,
        )
        await conn.execute(
            """
            INSERT INTO content.workspace_chunk_membership
                (workspace_id, canonical_chunk_id, workspace_zettel_id)
            VALUES ($1, $2, $3)
            """,
            ws_id, cc_id, wz_id,
        )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("content").rpc(
        "hybrid_search_chunks",
        {
            "p_workspace_id": str(ws_id),
            "p_query_text": "transformer attention",
            "p_query_embedding": _embedding_literal(0.0),
            "p_match_count": 5,
            "p_rrf_k": 60,
            "p_full_text_weight": 1.0,
            "p_semantic_weight": 1.0,
        },
    ).execute()
    rows = resp.data or []
    assert len(rows) == 1, f"expected 1 fts-only row, got {rows!r}"
    row = rows[0]
    assert row["fts_rank"] == 1
    assert row["semantic_rank"] is None
    expected = 1.0 / (60 + 1)
    assert math.isclose(row["rrf_score"], expected, rel_tol=1e-9), (
        f"rrf mismatch: got {row['rrf_score']}, expected {expected}"
    )


@pytest.mark.asyncio
async def test_hybrid_search_rrf_math(mint_user, asyncpg_pool):
    """Verify RRF math: a chunk that is FTS-rank-1 and semantic-rank-N
    must score 1/(60+1) + 1/(60+N) under default weights.

    Strategy:
      - Seed 5 chunks with embeddings 0.0, 0.01, ..., 0.04 (helper iterates
        with embedding_seed + i*0.01).
      - Only the LAST chunk (i=4) has a unique FTS token "antidisestablishmentarianism".
      - Query embedding = embedding_literal(0.0) -> closest to chunk 0; the
        last chunk (i=4) is therefore semantic rank 5.
      - Only chunk 4 matches FTS -> FTS rank 1.
      - Expected rrf_score for chunk 4 = 1/(60+1) + 1/(60+5).
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    contents = [
        "lorem ipsum dolor sit amet",
        "consectetur adipiscing elit",
        "sed do eiusmod tempor incididunt",
        "ut labore et dolore magna",
        # Only this chunk has the unique token; embedding_seed+0.04 puts it
        # furthest from the query embedding (seed 0.0).
        "antidisestablishmentarianism is a long word",
    ]
    _, _, chunks = await _seed_canonical_zettel_with_chunks(
        asyncpg_pool,
        workspace_id=ws_id,
        n_chunks=5,
        chunk_contents=contents,
    )
    target_chunk_id = chunks[4]

    client = get_v2_user_client(user.jwt)
    resp = client.schema("content").rpc(
        "hybrid_search_chunks",
        {
            "p_workspace_id": str(ws_id),
            "p_query_text": "antidisestablishmentarianism",
            "p_query_embedding": _embedding_literal(0.0),
            "p_match_count": 10,
            "p_rrf_k": 60,
            "p_full_text_weight": 1.0,
            "p_semantic_weight": 1.0,
        },
    ).execute()
    rows = resp.data or []
    assert len(rows) >= 1
    by_id = {uuid.UUID(r["canonical_chunk_id"]): r for r in rows}
    target_row = by_id.get(target_chunk_id)
    assert target_row is not None, (
        f"target chunk {target_chunk_id} missing from {rows!r}"
    )
    assert target_row["fts_rank"] == 1, (
        f"expected FTS rank 1 (only matching chunk), got {target_row['fts_rank']}"
    )
    assert target_row["semantic_rank"] == 5, (
        f"expected semantic rank 5 (furthest of 5), got {target_row['semantic_rank']}"
    )
    expected = (1.0 / (60 + 1)) + (1.0 / (60 + 5))
    assert math.isclose(target_row["rrf_score"], expected, rel_tol=1e-9), (
        f"rrf mismatch: got {target_row['rrf_score']}, expected {expected}"
    )


@pytest.mark.asyncio
async def test_hybrid_search_authz_denial(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]

    await _seed_canonical_zettel_with_chunks(
        asyncpg_pool, workspace_id=ws_id, n_chunks=1
    )

    client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("content").rpc(
            "hybrid_search_chunks",
            {
                "p_workspace_id": str(ws_id),
                "p_query_text": "anything",
                "p_query_embedding": _embedding_literal(0.0),
                "p_match_count": 5,
                "p_rrf_k": 60,
                "p_full_text_weight": 1.0,
                "p_semantic_weight": 1.0,
            },
        ).execute()
    assert _is_unauthorized(exc_info.value), \
        f"expected unauthorized, got {exc_info.value!r}"


# ===========================================================================
# 3. rag.resolve_effective_nodes_v2 (21_resolve_effective_nodes_rpc.sql)
# ===========================================================================


async def _seed_kasten_with_zettels(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    zettels: list[tuple[list[str], str]],  # (user_tags, source_type) per zettel
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Seed a kasten + n zettels in the workspace; return (kasten_id, [wz_id...])."""
    kasten_id = await _create_kasten(pool, workspace_id=workspace_id)
    wz_ids: list[uuid.UUID] = []
    for i, (tags, src) in enumerate(zettels):
        _, wz_id, _ = await _seed_canonical_zettel_with_chunks(
            pool,
            workspace_id=workspace_id,
            n_chunks=1,
            embedding_seed=i * 0.1,
            user_tags=tags,
            source_type=src,
        )
        wz_ids.append(wz_id)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
                VALUES ($1, $2, 'manual')
                """,
                kasten_id, wz_id,
            )
    return kasten_id, wz_ids


@pytest.mark.asyncio
async def test_resolve_effective_nodes_tag_mode_any(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    kasten_id, wz_ids = await _seed_kasten_with_zettels(
        asyncpg_pool, workspace_id=ws_id,
        zettels=[
            (["python", "rag"], "github"),
            (["python", "ml"], "web"),
            (["js"], "web"),
        ],
    )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "resolve_effective_nodes_v2",
        {
            "p_kasten_id": str(kasten_id),
            "p_tags": ["python"],
            "p_tag_mode": "any",
        },
    ).execute()
    rows = resp.data or []
    returned = {uuid.UUID(r["workspace_zettel_id"]) for r in rows}
    # First two have 'python', third does not.
    assert returned == {wz_ids[0], wz_ids[1]}, (
        f"expected first two zettels, got {returned!r}"
    )


@pytest.mark.asyncio
async def test_resolve_effective_nodes_tag_mode_all(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    kasten_id, wz_ids = await _seed_kasten_with_zettels(
        asyncpg_pool, workspace_id=ws_id,
        zettels=[
            (["python", "rag"], "github"),
            (["python", "ml"], "web"),
            (["python", "rag", "ml"], "web"),
        ],
    )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "resolve_effective_nodes_v2",
        {
            "p_kasten_id": str(kasten_id),
            "p_tags": ["python", "rag"],
            "p_tag_mode": "all",
        },
    ).execute()
    rows = resp.data or []
    returned = {uuid.UUID(r["workspace_zettel_id"]) for r in rows}
    # Only first and third have BOTH 'python' AND 'rag'.
    assert returned == {wz_ids[0], wz_ids[2]}, (
        f"expected first and third zettels, got {returned!r}"
    )


@pytest.mark.asyncio
async def test_resolve_effective_nodes_tag_mode_none(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    kasten_id, wz_ids = await _seed_kasten_with_zettels(
        asyncpg_pool, workspace_id=ws_id,
        zettels=[
            (["python"], "github"),
            (["js"], "web"),
            (["rust"], "web"),
        ],
    )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "resolve_effective_nodes_v2",
        {
            "p_kasten_id": str(kasten_id),
            "p_tags": ["python"],
            "p_tag_mode": "none",
        },
    ).execute()
    rows = resp.data or []
    returned = {uuid.UUID(r["workspace_zettel_id"]) for r in rows}
    assert returned == {wz_ids[1], wz_ids[2]}, (
        f"expected js and rust zettels, got {returned!r}"
    )


@pytest.mark.asyncio
async def test_resolve_effective_nodes_source_type_filter(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    kasten_id, wz_ids = await _seed_kasten_with_zettels(
        asyncpg_pool, workspace_id=ws_id,
        zettels=[
            (["x"], "github"),
            (["y"], "web"),
            (["z"], "youtube"),
        ],
    )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "resolve_effective_nodes_v2",
        {
            "p_kasten_id": str(kasten_id),
            "p_source_types": ["web", "youtube"],
        },
    ).execute()
    rows = resp.data or []
    returned = {uuid.UUID(r["workspace_zettel_id"]) for r in rows}
    assert returned == {wz_ids[1], wz_ids[2]}


@pytest.mark.asyncio
async def test_resolve_effective_nodes_combined_filters(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    kasten_id, wz_ids = await _seed_kasten_with_zettels(
        asyncpg_pool, workspace_id=ws_id,
        zettels=[
            (["python"], "github"),
            (["python"], "web"),
            (["js"], "web"),
        ],
    )

    client = get_v2_user_client(user.jwt)
    resp = client.schema("rag").rpc(
        "resolve_effective_nodes_v2",
        {
            "p_kasten_id": str(kasten_id),
            "p_tags": ["python"],
            "p_tag_mode": "any",
            "p_source_types": ["web"],
        },
    ).execute()
    rows = resp.data or []
    returned = {uuid.UUID(r["workspace_zettel_id"]) for r in rows}
    assert returned == {wz_ids[1]}, (
        f"expected only python+web zettel, got {returned!r}"
    )


@pytest.mark.asyncio
async def test_resolve_effective_nodes_invalid_tag_mode(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)

    client = get_v2_user_client(user.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("rag").rpc(
            "resolve_effective_nodes_v2",
            {
                "p_kasten_id": str(kasten_id),
                "p_tags": ["python"],
                "p_tag_mode": "garbage",
            },
        ).execute()
    msg = str(exc_info.value).lower()
    code = getattr(exc_info.value, "code", None)
    assert "invalid tag_mode" in msg or code == "22023" or "22023" in msg, (
        f"expected invalid_tag_mode (22023), got {exc_info.value!r}"
    )


@pytest.mark.asyncio
async def test_resolve_effective_nodes_authz_denial(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    kasten_id = await _create_kasten(asyncpg_pool, workspace_id=ws_id)

    client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        client.schema("rag").rpc(
            "resolve_effective_nodes_v2",
            {
                "p_kasten_id": str(kasten_id),
            },
        ).execute()
    assert _is_unauthorized(exc_info.value), \
        f"expected unauthorized, got {exc_info.value!r}"


# ===========================================================================
# 4a. kg.kg_node_aliases table (22_kg_aliases_table.sql)
# ===========================================================================


async def _seed_kg_node(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    canonical_name: str,
    aliases: list[tuple[str, str]] | None = None,  # (alias, kind)
    node_type: str = "concept",
) -> int:
    """Insert a kg_node + optional aliases via service-role; return kg_node_id."""
    slug = f"{canonical_name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO kg.kg_nodes (workspace_id, type, canonical_name, slug)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            workspace_id, node_type, canonical_name, slug,
        )
        node_id = row["id"]
        for alias, kind in (aliases or []):
            await conn.execute(
                """
                INSERT INTO kg.kg_node_aliases (kg_node_id, alias, alias_kind)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                """,
                node_id, alias, kind,
            )
        return node_id


@pytest.mark.asyncio
async def test_kg_node_aliases_rls_workspace_scope(mint_user, asyncpg_pool):
    """A user can SELECT aliases for nodes in their workspace, but not others."""
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]

    node_id = await _seed_kg_node(
        asyncpg_pool, workspace_id=ws_id,
        canonical_name="Transformer Architecture",
        aliases=[("transformers", "surface_form"), ("xfmr", "abbreviation")],
    )

    # Owner sees both aliases via supabase-py user client.
    owner_client = get_v2_user_client(owner.jwt)
    resp = owner_client.schema("kg").table("kg_node_aliases").select(
        "alias,alias_kind"
    ).eq("kg_node_id", node_id).execute()
    rows = resp.data or []
    aliases = {(r["alias"], r["alias_kind"]) for r in rows}
    assert aliases == {("transformers", "surface_form"), ("xfmr", "abbreviation")}, (
        f"owner expected both aliases, got {aliases!r}"
    )

    # Intruder sees nothing (RLS blocks).
    intruder_client = get_v2_user_client(intruder.jwt)
    resp = intruder_client.schema("kg").table("kg_node_aliases").select(
        "alias"
    ).eq("kg_node_id", node_id).execute()
    assert (resp.data or []) == [], (
        f"intruder should see no aliases, got {resp.data!r}"
    )
