-- iter-10 P5 / Q6,Q7: dense-only kasten-scoped fallback for hybrid recall miss.
-- When `rag_hybrid_search` returns zero rows for every variant AND the kasten
-- has members, this RPC runs a single high-precision dense pass scoped to all
-- effective nodes so cross-encoder still has SOMETHING to rerank. Never the
-- primary path; gated server-side by RAG_DENSE_FALLBACK_ENABLED + the empty-
-- pool check in hybrid.retrieve.
--
-- Schema notes (mirrors rag_fetch_anchor_seeds for cross-tenant safety):
--   * kg_nodes PK is (user_id, id) — column is `id`, not `node_id`.
--   * kg_node_chunks joins on (node_id, user_id) via the same index path.
--   * effective_nodes is the post-resolve list — already user-allowlisted in
--     hybrid.retrieve via rag_resolve_effective_nodes.
BEGIN;

CREATE OR REPLACE FUNCTION rag_dense_recall(
    p_user_id         uuid,
    p_effective_nodes text[],
    p_query_embedding vector(768),
    p_limit           int DEFAULT 8
) RETURNS TABLE (
    kind        text,
    node_id     text,
    chunk_id    uuid,
    chunk_idx   int,
    name        text,
    source_type text,
    url         text,
    content     text,
    tags        text[],
    rrf_score   double precision
)
LANGUAGE sql STABLE AS $$
    SELECT
        'chunk'::text                                      AS kind,
        n.id                                               AS node_id,
        kc.id                                              AS chunk_id,
        kc.chunk_idx,
        COALESCE(n.name, n.id)                             AS name,
        COALESCE(n.source_type, 'web')                     AS source_type,
        COALESCE(n.url, '')                                AS url,
        COALESCE(kc.content, '')                           AS content,
        COALESCE(n.tags, ARRAY[]::text[])                  AS tags,
        1 - (kc.embedding <=> p_query_embedding)           AS rrf_score
    FROM kg_node_chunks kc
    JOIN kg_nodes n
      ON n.id = kc.node_id
     AND n.user_id = kc.user_id
    WHERE n.user_id = p_user_id
      AND n.id = ANY(p_effective_nodes)
    ORDER BY kc.embedding <=> p_query_embedding ASC
    LIMIT p_limit;
$$;

GRANT EXECUTE ON FUNCTION rag_dense_recall(uuid, text[], vector, int) TO anon, authenticated;

COMMIT;
