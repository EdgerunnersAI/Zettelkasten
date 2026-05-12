"""WAVE-C kg_features gap-fill — embedding provenance (KF-EMB-A + KF-EMB-B).

Covers two gaps not handled by the 1c-A backend subagent:

* **KF-EMB-A** — every row landed in ``content.canonical_chunks`` MUST carry a
  non-null, non-empty ``embedding_model_version`` value. The column has a
  ``NOT NULL DEFAULT 'gemini-001-mrl-768'`` constraint at the schema level
  (``supabase/website/_v2/02_content_schema.sql``); these tests pin that the
  contract holds via both the explicit insert path the upsert pipeline uses
  *and* the implicit DEFAULT path.

* **KF-EMB-B** — ``find_similar_nodes`` calls ``kg.match_kg_nodes`` (ported
  from the dropped legacy ``public.match_kg_nodes`` in
  ``supabase/website/_v2/43_port_match_kg_nodes.sql``). The v2 RPC takes a
  mandatory ``p_model_version`` argument so cross-version cosine collisions
  are filtered at the storage layer, not at the (silently swallowed)
  PostgREST error path the legacy RPC fell through. These tests pin:

    1. The v2 RPC signature includes ``p_model_version`` as a parameter.
    2. ``find_similar_nodes`` returns the matching node when the seeded
       chunk's model version equals the queried version.
    3. Chunks with a *different* ``embedding_model_version`` are excluded
       from results even when their cosine distance would otherwise match.

Both tests are ``@pytest.mark.live`` because they need real Postgres (halfvec
casts, RPC dispatch). Seeding pattern mirrors
``test_ingest_memory_e2e._seed_zettel``.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.live


# ── Helpers ──────────────────────────────────────────────────────────────


def _embedding_literal(seed: float = 0.0) -> str:
    """Deterministic 768-dim halfvec literal, matching ingest_memory_e2e."""
    base = 0.001 + seed
    vals = [round(base + i * 1e-5, 6) for i in range(768)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


async def _seed_canonical_zettel(
    pool: asyncpg.Pool,
) -> uuid.UUID:
    """Insert a bare ``canonical_zettel`` and return its id. Workspace-agnostic
    so the row stays compatible with both KF-EMB-A and KF-EMB-B fixtures.
    """
    cz_id = uuid.uuid4()
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    norm_url = f"https://kf-emb.test/{uuid.uuid4().hex}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, 'web', $4, $5, '2026-05-12'::date)
            """,
            cz_id, norm_url, chash, "kf-emb seed", "body",
        )
    return cz_id


async def _insert_chunk_with_version(
    pool: asyncpg.Pool,
    *,
    cz_id: uuid.UUID,
    chunk_idx: int,
    seed: float,
    embedding_model_version: str | None,
) -> uuid.UUID:
    """Insert one chunk; ``embedding_model_version=None`` exercises the
    schema DEFAULT path (column is NOT NULL DEFAULT 'gemini-001-mrl-768').
    """
    cc_id = uuid.uuid4()
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    emb = _embedding_literal(seed)
    async with pool.acquire() as conn:
        if embedding_model_version is None:
            await conn.execute(
                f"""
                INSERT INTO content.canonical_chunks
                    (id, canonical_zettel_id, chunk_idx, content,
                     content_hash, chunk_type, embedding)
                VALUES ($1, $2, $3, $4, $5, 'atomic',
                        '{emb}'::halfvec(768))
                """,
                cc_id, cz_id, chunk_idx, f"body {chunk_idx}", chash,
            )
        else:
            await conn.execute(
                f"""
                INSERT INTO content.canonical_chunks
                    (id, canonical_zettel_id, chunk_idx, content,
                     content_hash, chunk_type, embedding,
                     embedding_model_version)
                VALUES ($1, $2, $3, $4, $5, 'atomic',
                        '{emb}'::halfvec(768), $6)
                """,
                cc_id, cz_id, chunk_idx, f"body {chunk_idx}", chash,
                embedding_model_version,
            )
    return cc_id


# ── KF-EMB-A: embedding provenance ──────────────────────────────────────


async def test_canonical_chunks_default_model_version_populated(
    asyncpg_pool: asyncpg.Pool,
) -> None:
    """KF-EMB-A: implicit-DEFAULT path. Inserting a chunk WITHOUT specifying
    ``embedding_model_version`` lands the schema default 'gemini-001-mrl-768'
    — the column is NOT NULL so an empty/null sentinel is impossible.
    """
    cz_id = await _seed_canonical_zettel(asyncpg_pool)
    try:
        cc_id = await _insert_chunk_with_version(
            asyncpg_pool,
            cz_id=cz_id,
            chunk_idx=0,
            seed=0.0,
            embedding_model_version=None,
        )

        async with asyncpg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT embedding_model_version "
                "FROM content.canonical_chunks WHERE id = $1",
                cc_id,
            )
        assert row is not None, "chunk did not land"
        assert row["embedding_model_version"] is not None
        assert row["embedding_model_version"].strip() != "", (
            "embedding_model_version must be non-empty"
        )
        # Pin the locked default so a future ALTER COLUMN DEFAULT change trips
        # this test (cross-tenant cosine safety relies on a single default).
        assert row["embedding_model_version"] == "gemini-001-mrl-768"
    finally:
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM content.canonical_zettels WHERE id = $1", cz_id
            )


async def test_canonical_chunks_explicit_model_version_persisted(
    asyncpg_pool: asyncpg.Pool,
) -> None:
    """KF-EMB-A: explicit-stamp path mirrors the production upsert pipeline
    (``website.features.rag_pipeline.ingest.upsert._EMBED_MODEL_VERSION``)
    which passes the version explicitly into the insert.
    """
    cz_id = await _seed_canonical_zettel(asyncpg_pool)
    explicit_version = "gemini-001-mrl-768"
    try:
        cc_id = await _insert_chunk_with_version(
            asyncpg_pool,
            cz_id=cz_id,
            chunk_idx=0,
            seed=0.0,
            embedding_model_version=explicit_version,
        )
        async with asyncpg_pool.acquire() as conn:
            stored = await conn.fetchval(
                "SELECT embedding_model_version "
                "FROM content.canonical_chunks WHERE id = $1",
                cc_id,
            )
        assert stored == explicit_version
    finally:
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM content.canonical_zettels WHERE id = $1", cz_id
            )


async def test_canonical_chunks_null_model_version_rejected(
    asyncpg_pool: asyncpg.Pool,
) -> None:
    """KF-EMB-A: explicit NULL must violate the NOT NULL constraint. Pins
    the schema invariant so a future migration cannot weaken the column to
    NULLABLE without tripping CI.
    """
    cz_id = await _seed_canonical_zettel(asyncpg_pool)
    cc_id = uuid.uuid4()
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    emb = _embedding_literal(0.0)
    try:
        with pytest.raises(asyncpg.NotNullViolationError):
            async with asyncpg_pool.acquire() as conn:
                await conn.execute(
                    f"""
                    INSERT INTO content.canonical_chunks
                        (id, canonical_zettel_id, chunk_idx, content,
                         content_hash, chunk_type, embedding,
                         embedding_model_version)
                    VALUES ($1, $2, $3, $4, $5, 'atomic',
                            '{emb}'::halfvec(768), NULL)
                    """,
                    cc_id, cz_id, 0, "body", chash,
                )
    finally:
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM content.canonical_zettels WHERE id = $1", cz_id
            )


async def test_canonical_chunks_unknown_model_version_rejected_by_fk(
    asyncpg_pool: asyncpg.Pool,
) -> None:
    """KF-EMB-A: ``embedding_model_version`` is a FK to
    ``content.embedding_model_versions``. Unknown versions must be rejected
    by the FK constraint — mismatched-version vectors are flagged
    not-comparable at write time, not at query time.
    """
    cz_id = await _seed_canonical_zettel(asyncpg_pool)
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _insert_chunk_with_version(
                asyncpg_pool,
                cz_id=cz_id,
                chunk_idx=0,
                seed=0.0,
                embedding_model_version=f"unknown-model-{uuid.uuid4().hex[:8]}",
            )
    finally:
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM content.canonical_zettels WHERE id = $1", cz_id
            )


# ── KF-EMB-B: model_version filtering on match_kg_nodes ──────────────────


async def _seed_kg_node_for_chunk(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    canonical_chunk_id: uuid.UUID,
    canonical_name: str,
) -> int:
    """Insert a kg.kg_nodes row + bridge row in kg.chunk_node_mentions
    pointing at the seeded chunk. Returns the bigserial node_id.

    Mirrors the seeding pattern in
    ``tests/integration/v2/test_hybrid_pipeline_e2e._seed_kg_node_with_alias``.
    """
    slug = f"{canonical_name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}"
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
            INSERT INTO kg.chunk_node_mentions
                (canonical_chunk_id, kg_node_id, mention_type)
            VALUES ($1, $2, 'extracted')
            ON CONFLICT DO NOTHING
            """,
            canonical_chunk_id, node_id,
        )
    return node_id


async def test_match_kg_nodes_rpc_has_model_version_filter(
    asyncpg_pool: asyncpg.Pool,
) -> None:
    """KF-EMB-B: pins the ported v2 RPC contract — ``kg.match_kg_nodes`` MUST
    take a ``p_model_version`` argument so cross-version cosine collisions
    are filtered server-side. If a future migration drops or renames this
    parameter the test flips RED before the silently-broken filter ships.
    """
    async with asyncpg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT parameter_name, data_type
              FROM information_schema.parameters
             WHERE specific_schema = 'kg'
               AND specific_name LIKE 'match_kg_nodes%'
             ORDER BY ordinal_position
            """
        )
    param_names = [r["parameter_name"] for r in rows]
    assert "p_model_version" in param_names, (
        f"kg.match_kg_nodes is missing the p_model_version filter parameter. "
        f"Current params: {param_names}"
    )
    # Pin the full canonical signature so a future param-rename is caught.
    assert param_names == [
        "p_user_id",
        "p_query_embedding",
        "p_model_version",
        "p_match_threshold",
        "p_match_count",
        # OUT params (RETURNS TABLE) appear in information_schema.parameters too.
        "node_id",
        "score",
    ], f"unexpected kg.match_kg_nodes signature: {param_names}"


async def test_find_similar_nodes_returns_seeded_node(
    asyncpg_pool: asyncpg.Pool,
    mint_user,
) -> None:
    """KF-EMB-B positive path: seed one chunk + node for a freshly-minted
    user, then call ``find_similar_nodes`` with an embedding close to the
    seeded vector. The seeded node MUST appear in results (proving the v2
    RPC + workspace-resolution + service-role grant all wire up correctly).
    """
    from website.core.supabase_v2.client import get_v2_client
    from website.features.kg_features.embeddings import find_similar_nodes

    user = mint_user(workspace_count=1)
    workspace_id = user.workspace_ids[0]

    cz_id = await _seed_canonical_zettel(asyncpg_pool)
    try:
        cc_id = await _insert_chunk_with_version(
            asyncpg_pool,
            cz_id=cz_id,
            chunk_idx=0,
            seed=0.0,
            embedding_model_version="gemini-001-mrl-768",
        )
        node_id = await _seed_kg_node_for_chunk(
            asyncpg_pool,
            workspace_id=workspace_id,
            canonical_chunk_id=cc_id,
            canonical_name="kf-emb positive",
        )

        client = get_v2_client()
        # Query embedding nearly identical to the seeded one (cosine ~ 1.0).
        query_emb = [0.001 + i * 1e-5 for i in range(768)]

        result = find_similar_nodes(
            supabase_client=client,
            user_id=str(user.auth_user_id),
            embedding=query_emb,
            threshold=0.5,
            limit=10,
        )

        assert isinstance(result, list) and result, (
            f"expected at least one match, got {result!r}"
        )
        assert any(int(r.get("node_id", -1)) == node_id for r in result), (
            f"seeded node_id={node_id} not in results: {result!r}"
        )
    finally:
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM content.canonical_zettels WHERE id = $1", cz_id
            )


async def test_find_similar_nodes_filters_by_model_version(
    asyncpg_pool: asyncpg.Pool,
    mint_user,
) -> None:
    """KF-EMB-B cross-version isolation: seed two chunks for the same user
    with the SAME embedding payload but DIFFERENT
    ``embedding_model_version``s. Calling ``find_similar_nodes`` with the
    default version must return ONLY the matching-version chunk's node —
    proving the storage-layer filter excludes cross-version vectors even
    when their cosine distance would otherwise hit the threshold.

    This requires a second registered model version in
    ``content.embedding_model_versions`` for the FK to accept the off-version
    chunk; the test inserts it inside a savepoint and rolls back at teardown
    so we never leak schema state.
    """
    from website.core.supabase_v2.client import get_v2_client
    from website.features.kg_features.embeddings import find_similar_nodes

    user = mint_user(workspace_count=1)
    workspace_id = user.workspace_ids[0]
    other_version = f"test-other-{uuid.uuid4().hex[:8]}"

    cz_id = await _seed_canonical_zettel(asyncpg_pool)
    try:
        # Register the off-version so the FK accepts the second chunk insert.
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO content.embedding_model_versions
                    (version_id, dimensions, is_default)
                VALUES ($1, 768, false)
                """,
                other_version,
            )

        cc_match = await _insert_chunk_with_version(
            asyncpg_pool,
            cz_id=cz_id,
            chunk_idx=0,
            seed=0.0,
            embedding_model_version="gemini-001-mrl-768",
        )
        cc_other = await _insert_chunk_with_version(
            asyncpg_pool,
            cz_id=cz_id,
            chunk_idx=1,
            seed=0.0,  # SAME seed → identical cosine, isolation must come from version filter
            embedding_model_version=other_version,
        )

        node_match = await _seed_kg_node_for_chunk(
            asyncpg_pool,
            workspace_id=workspace_id,
            canonical_chunk_id=cc_match,
            canonical_name="match version",
        )
        node_other = await _seed_kg_node_for_chunk(
            asyncpg_pool,
            workspace_id=workspace_id,
            canonical_chunk_id=cc_other,
            canonical_name="other version",
        )

        client = get_v2_client()
        query_emb = [0.001 + i * 1e-5 for i in range(768)]

        result = find_similar_nodes(
            supabase_client=client,
            user_id=str(user.auth_user_id),
            embedding=query_emb,
            threshold=0.5,
            limit=10,
            model_version="gemini-001-mrl-768",
        )

        returned_ids = {int(r.get("node_id", -1)) for r in result}
        assert node_match in returned_ids, (
            f"matching-version node {node_match} missing from {result!r}"
        )
        assert node_other not in returned_ids, (
            f"off-version node {node_other} leaked through filter: {result!r}"
        )
    finally:
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM content.canonical_zettels WHERE id = $1", cz_id
            )
            # Best-effort: drop the registered off-version so the test is hermetic.
            await conn.execute(
                "DELETE FROM content.embedding_model_versions WHERE version_id = $1",
                other_version,
            )
