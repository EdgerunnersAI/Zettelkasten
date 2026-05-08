-- iter-09 RES-7 / Q10: anchor-seed RPC. Returns seed candidates for the
-- supplied anchor node ids restricted to the sandbox's members. INNER JOIN
-- on rag_sandbox_members is mandatory for cross-tenant safety.
--
-- Returns one row per anchor node — the *best* chunk per node ranked by
-- cosine similarity to the query embedding, with enough columns to build
-- a RetrievalCandidate downstream so cross-encoder rerank can decide final
-- ordering. Floor for rrf is applied in Python (RAG_ANCHOR_SEED_FLOOR_RRF).
--
-- Schema notes:
--   * kg_nodes PK is (user_id, id) — column is `id`, not `node_id`.
--   * kg_node_chunks join on (node_id, user_id) mirrors the index used by
--     rag_kasten_chunk_counts.
BEGIN;

CREATE OR REPLACE FUNCTION rag_fetch_anchor_seeds(
    p_sandbox_id      uuid,
    p_anchor_nodes    text[],
    p_query_embedding vector(768)
) RETURNS TABLE (
    node_id     text,
    chunk_id    uuid,
    chunk_idx   int,
    kind        text,
    name        text,
    source_type text,
    url         text,
    content     text,
    tags        text[],
    score       double precision
)
LANGUAGE sql STABLE AS $$
    WITH ranked AS (
        SELECT
            m.node_id,
            kc.id                                              AS chunk_id,
            kc.chunk_idx,
            'chunk'::text                                      AS kind,
            COALESCE(n.name, m.node_id)                        AS name,
            COALESCE(n.source_type, 'web')                     AS source_type,
            COALESCE(n.url, '')                                AS url,
            COALESCE(kc.content, '')                           AS content,
            COALESCE(n.tags, ARRAY[]::text[])                  AS tags,
            1 - (kc.embedding <=> p_query_embedding)           AS score,
            ROW_NUMBER() OVER (
                PARTITION BY m.node_id
                ORDER BY kc.embedding <=> p_query_embedding ASC
            ) AS rn
        FROM rag_sandbox_members m
        INNER JOIN kg_node_chunks kc
          ON kc.node_id = m.node_id
         AND kc.user_id = m.user_id
        LEFT JOIN kg_nodes n
          ON n.id = m.node_id
         AND n.user_id = m.user_id
        WHERE m.sandbox_id = p_sandbox_id
          AND m.node_id = ANY(p_anchor_nodes)
    )
    SELECT node_id, chunk_id, chunk_idx, kind, name, source_type, url, content, tags, score
    FROM ranked
    WHERE rn = 1
    ORDER BY score DESC
    LIMIT 8;
$$;

GRANT EXECUTE ON FUNCTION rag_fetch_anchor_seeds(uuid, text[], vector) TO anon, authenticated;

COMMIT;
