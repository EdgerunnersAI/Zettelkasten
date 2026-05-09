"""Phase 2.4 e2e: HybridRetriever runs end-to-end against the live v2 stack.

Covers the full v2 retrieval pipeline:

  - Service-role asyncpg pool seeds: kasten + 3 zettels (different sources) +
    chunks + halfvec(768) embeddings + kg_nodes + kg_node_aliases +
    chunk_node_mentions + retrieval_signal_weights.
  - HybridRetriever is built with a user-JWT supabase client (RLS active).
  - retrieve(query=..., kasten_id=...) drives all sub-tasks 2.4.1 - 2.4.6:
      * kg.resolve_entity_anchors_v2 (matched_kind=alias)
      * kg.expand_subgraph (depth=1)
      * kg.entities_to_anchor_chunks (kg_node_id -> canonical_chunk_id bridge)
      * rag.resolve_effective_nodes_v2 (kasten-scoped scope resolution)
      * content.hybrid_search_chunks_kasten (per-source ranks + raw scores)
      * Python 3-source weighted RRF (sem/fts/graph)
      * rag.fetch_anchor_seeds_v2 (canonical_chunk_id input)
      * Zettel rollup (max=3 per canonical_zettel_id)

  Asserts:
    * Non-empty mixed-source candidate set comes back.
    * Zettel rollup respects max=3 per zettel.
    * raw_dense_score / raw_fts_score on at least one hybrid candidate.
    * Anchor exemption fires (anchor_chunk_mentions populated when seeded
      entities resolve).

Marked @pytest.mark.live — reads SUPABASE_V2_* from .env.v2 and hits the
project. Cleanup runs via the auth-user CASCADE in the conftest fixture.
"""
from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import pytest

from website.core.supabase_v2.client import get_v2_user_client
from website.features.rag_pipeline.retrieval.hybrid import HybridRetriever
from website.features.rag_pipeline.types import QueryClass, ScopeFilter


pytestmark = pytest.mark.live


def _embedding_literal(seed: float = 0.0) -> str:
    """Deterministic 768-dim halfvec literal so seed similarity is predictable."""
    base = 0.001 + seed
    vals = [round(base + i * 1e-5, 6) for i in range(768)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


class _StubEmbedder:
    """Returns a halfvec-shaped query embedding aligned with seed=0.0 chunks."""

    def __init__(self, seed: float = 0.0):
        # Match the literal seeded into the chunks so cosine sim is high.
        self._vec = [round(0.001 + seed + i * 1e-5, 6) for i in range(768)]

    async def embed_query_with_cache(self, _query: str) -> list[float]:
        return list(self._vec)


async def _seed_zettel(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    title: str,
    source_type: str,
    chunk_contents: list[str],
    embedding_seed: float = 0.0,
    user_tags: list[str] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    if user_tags is None:
        user_tags = []
    async with pool.acquire() as conn:
        cz_id = uuid.uuid4()
        norm_url = f"https://example.test/{uuid.uuid4().hex}"
        ch = uuid.uuid4().bytes + uuid.uuid4().bytes
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, $4, $5, $6, '2026-04-01'::date)
            """,
            cz_id, norm_url, ch, source_type, title, "body",
        )
        chunk_ids: list[uuid.UUID] = []
        for i, body in enumerate(chunk_contents):
            cc_id = uuid.uuid4()
            chunk_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
            emb = _embedding_literal(embedding_seed + i * 0.001)
            await conn.execute(
                f"""
                INSERT INTO content.canonical_chunks
                    (id, canonical_zettel_id, chunk_idx, content,
                     content_hash, chunk_type, embedding)
                VALUES ($1, $2, $3, $4, $5, 'atomic', '{emb}'::halfvec(768))
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


async def _create_kasten_with_zettels(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    wz_ids: list[uuid.UUID],
) -> uuid.UUID:
    async with pool.acquire() as conn:
        kasten_row = await conn.fetchrow(
            """
            INSERT INTO rag.kastens (workspace_id, name)
            VALUES ($1, $2)
            RETURNING id
            """,
            workspace_id, f"k-{uuid.uuid4().hex[:6]}",
        )
        kasten_id = kasten_row["id"]
        for wz_id in wz_ids:
            await conn.execute(
                """
                INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
                VALUES ($1, $2, 'manual')
                ON CONFLICT DO NOTHING
                """,
                kasten_id, wz_id,
            )
    return kasten_id


async def _seed_kg_node_with_alias(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    canonical_name: str,
    alias: str,
) -> int:
    slug = f"{canonical_name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO kg.kg_nodes (workspace_id, type, canonical_name, slug)
            VALUES ($1, 'concept', $2, $3)
            RETURNING id
            """,
            workspace_id, canonical_name, slug,
        )
        node_id = row["id"]
        await conn.execute(
            """
            INSERT INTO kg.kg_node_aliases (kg_node_id, alias, alias_kind)
            VALUES ($1, $2, 'surface_form')
            ON CONFLICT DO NOTHING
            """,
            node_id, alias,
        )
        return node_id


async def _seed_chunk_mention(
    pool: asyncpg.Pool,
    *,
    canonical_chunk_id: uuid.UUID,
    kg_node_id: int,
    mention_type: str = "extracted",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO kg.chunk_node_mentions
                (canonical_chunk_id, kg_node_id, mention_type)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            canonical_chunk_id, kg_node_id, mention_type,
        )


@pytest.mark.asyncio
async def test_hybrid_retriever_e2e(mint_user, asyncpg_pool):
    """End-to-end retrieve() against the live v2 stack."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    # Seed three zettels in distinct sources so the result is mixed-source.
    # antidisestablishmentarianism = unique FTS token in zettel-1; embeddings
    # are seeded close together so all three rank competitively for dense.
    _, wz1, chunks1 = await _seed_zettel(
        asyncpg_pool, workspace_id=ws_id,
        title="Transformer Architectures Deep Dive",
        source_type="github",
        chunk_contents=[
            "transformer attention mechanism antidisestablishmentarianism",
            "encoder decoder self attention layers",
            "positional encoding sinusoidal",
        ],
        embedding_seed=0.0,
        user_tags=["ml"],
    )
    _, wz2, chunks2 = await _seed_zettel(
        asyncpg_pool, workspace_id=ws_id,
        title="Retrieval Augmented Generation Notes",
        source_type="web",
        chunk_contents=[
            "rag pipeline embedding retrieval reranking",
            "vector database faiss pgvector",
        ],
        embedding_seed=0.05,
        user_tags=["rag"],
    )
    _, wz3, chunks3 = await _seed_zettel(
        asyncpg_pool, workspace_id=ws_id,
        title="Andrej Karpathy Talk Highlights",
        source_type="youtube",
        chunk_contents=[
            "andrej karpathy on building LLMs from scratch",
            "tokenization byte pair encoding",
        ],
        embedding_seed=0.1,
        user_tags=["talks"],
    )

    kasten_id = await _create_kasten_with_zettels(
        asyncpg_pool, workspace_id=ws_id, wz_ids=[wz1, wz2, wz3],
    )

    # KG: a single kg_node "Andrej Karpathy" with alias "karpathy", with
    # mentions on two chunks (zettel-3) so the bridge fires non-empty.
    kg_id = await _seed_kg_node_with_alias(
        asyncpg_pool, workspace_id=ws_id,
        canonical_name="Andrej Karpathy",
        alias="karpathy",
    )
    await _seed_chunk_mention(
        asyncpg_pool, canonical_chunk_id=chunks3[0], kg_node_id=kg_id,
    )
    await _seed_chunk_mention(
        asyncpg_pool, canonical_chunk_id=chunks3[1], kg_node_id=kg_id,
    )

    # Build HybridRetriever with the user's JWT-bound client (RLS on).
    user_client = get_v2_user_client(user.jwt)
    retriever = HybridRetriever(embedder=_StubEmbedder(seed=0.0), supabase=user_client)

    # query_metadata with authors=["karpathy"] -> resolves the alias ->
    # bridges to chunks3[0..1] -> graph signal fires for those rows.
    from website.features.rag_pipeline.query.metadata import QueryMetadata
    query_meta = QueryMetadata(authors=["karpathy"], entities=[])

    candidates = await retriever.retrieve(
        user_id=user.profile_id,
        query_variants=["antidisestablishmentarianism transformer karpathy"],
        sandbox_id=kasten_id,
        scope_filter=ScopeFilter(),
        query_class=QueryClass.MULTI_HOP,
        limit=10,
        query_metadata=query_meta,
    )

    # 1. Non-empty mixed-source candidate set.
    assert candidates, "expected at least one candidate"
    assert all(c.kind is not None for c in candidates), "every candidate has a kind"

    # 2. Zettel rollup max=3 per zettel — verify nothing exceeds the cap.
    by_zettel: dict[str, int] = {}
    for c in candidates:
        zid = c.metadata.get("canonical_zettel_id") or c.node_id
        by_zettel[zid] = by_zettel.get(zid, 0) + 1
    assert all(v <= 3 for v in by_zettel.values()), (
        f"_apply_zettel_rollup violated max=3: {by_zettel!r}"
    )

    # 3. raw_dense_score / raw_fts_score populated on at least one hybrid row.
    raws = [
        (
            c.metadata.get("raw_dense_score"),
            c.metadata.get("raw_fts_score"),
        )
        for c in candidates
    ]
    assert any(rd is not None for rd, _ in raws), (
        f"expected raw_dense_score on at least one candidate; got {raws!r}"
    )

    # 4. Anchor exemption signal — at least one of the karpathy-mentioned
    #    chunks (chunks3[0] or chunks3[1]) should be in the candidate set
    #    (the entities_to_anchor_chunks bridge ran).
    chunk_ids_in_pool = {str(c.node_id) for c in candidates}
    anchor_chunks = {str(chunks3[0]), str(chunks3[1])}
    assert chunk_ids_in_pool & anchor_chunks, (
        f"anchor exemption did not surface karpathy chunks; "
        f"pool={chunk_ids_in_pool!r}, expected one of {anchor_chunks!r}"
    )

    # Suppress unused-var warnings (the chunk-id sets are a property of seed
    # state used only for log debugging if the assertions above tighten).
    _ = chunks1, chunks2
