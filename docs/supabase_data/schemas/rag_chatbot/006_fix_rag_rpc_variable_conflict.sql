-- ============================================================================
-- 006_fix_rag_rpc_variable_conflict.sql
-- Fixes: "column reference node_id is ambiguous" (42702) raised by
-- rag_resolve_effective_nodes and rag_hybrid_search when called from
-- /api/rag/adhoc.
--
-- Root cause: the functions use RETURNS TABLE(..., node_id text, ...) which
-- declares implicit OUT parameters with the same names as real columns. In
-- PL/pgSQL, unqualified references inside the body are ambiguous between the
-- OUT param and the column (postgres 14+ enforces this strictly).
--
-- Fix: add `#variable_conflict use_column` pragma so column references win,
-- matching the original intent of the CTE body. Behaviour is otherwise
-- identical — only the language rule changes.
-- ============================================================================

CREATE OR REPLACE FUNCTION rag_resolve_effective_nodes(
    p_user_id      uuid,
    p_sandbox_id   uuid    DEFAULT NULL,
    p_node_ids     text[]  DEFAULT NULL,
    p_tags         text[]  DEFAULT NULL,
    p_tag_mode     text    DEFAULT 'all',
    p_source_types text[]  DEFAULT NULL
)
RETURNS TABLE (node_id text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = ''
SET statement_timeout = '3s'
AS $$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    WITH base AS (
        SELECT CASE
                 WHEN p_sandbox_id IS NULL THEN n.id
                 ELSE m.node_id
               END AS nid
        FROM public.kg_nodes n
        LEFT JOIN public.rag_sandbox_members m
               ON m.sandbox_id = p_sandbox_id
              AND m.node_id    = n.id
              AND m.user_id    = p_user_id
        WHERE n.user_id = p_user_id
          AND (p_sandbox_id IS NULL OR m.sandbox_id IS NOT NULL)
    )
    SELECT DISTINCT b.nid AS node_id
    FROM base b
    JOIN public.kg_nodes n ON n.user_id = p_user_id AND n.id = b.nid
    WHERE (p_node_ids     IS NULL OR n.id          = ANY(p_node_ids))
      AND (p_tags         IS NULL OR (
           (p_tag_mode = 'all' AND n.tags @> p_tags) OR
           (p_tag_mode = 'any' AND n.tags && p_tags)
           ))
      AND (p_source_types IS NULL OR n.source_type = ANY(p_source_types));
END;
$$;


CREATE OR REPLACE FUNCTION rag_hybrid_search(
    p_user_id          uuid,
    p_query_text       text,
    p_query_embedding  vector(768),
    p_effective_nodes  text[]  DEFAULT NULL,
    p_limit            int     DEFAULT 30,
    p_semantic_weight  float   DEFAULT 0.5,
    p_fulltext_weight  float   DEFAULT 0.3,
    p_graph_weight     float   DEFAULT 0.2,
    p_rrf_k            int     DEFAULT 60,
    p_graph_depth      int     DEFAULT 1,
    p_recency_decay    float   DEFAULT 0.0
)
RETURNS TABLE (
    kind           text,
    node_id        text,
    chunk_id       uuid,
    chunk_idx      int,
    name           text,
    source_type    text,
    url            text,
    content        text,
    tags           text[],
    metadata       jsonb,
    rrf_score      float
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = 'public'
SET statement_timeout = '5s'
AS $$
#variable_conflict use_column
BEGIN
    PERFORM set_config('hnsw.ef_search',       '100',          true);
    PERFORM set_config('hnsw.iterative_scan',  'strict_order', true);
    PERFORM set_config('hnsw.max_scan_tuples', '20000',        true);

    RETURN QUERY
    WITH RECURSIVE
    dense_summary AS (
        SELECT
            'summary'::text                                       AS kind,
            n.id                                                  AS node_id,
            NULL::uuid                                            AS chunk_id,
            0                                                     AS chunk_idx,
            n.name, n.source_type, n.url, n.summary AS content, n.tags, n.metadata,
            ROW_NUMBER() OVER (ORDER BY n.embedding <=> p_query_embedding) AS rank
        FROM kg_nodes n
        WHERE n.user_id = p_user_id
          AND (p_effective_nodes IS NULL OR n.id = ANY(p_effective_nodes))
          AND n.embedding IS NOT NULL
        ORDER BY n.embedding <=> p_query_embedding
        LIMIT p_limit * 3
    ),
    dense_chunk AS (
        SELECT
            'chunk'::text                                         AS kind,
            c.node_id                                             AS node_id,
            c.id                                                  AS chunk_id,
            c.chunk_idx                                           AS chunk_idx,
            n.name, n.source_type, n.url,
            c.content                                             AS content,
            n.tags, c.metadata,
            ROW_NUMBER() OVER (ORDER BY c.embedding <=> p_query_embedding) AS rank
        FROM kg_node_chunks c
        JOIN kg_nodes n ON n.user_id = p_user_id AND n.id = c.node_id
        WHERE c.user_id = p_user_id
          AND (p_effective_nodes IS NULL OR c.node_id = ANY(p_effective_nodes))
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> p_query_embedding
        LIMIT p_limit * 3
    ),
    fts_summary AS (
        SELECT
            'summary'::text                                       AS kind,
            n.id, NULL::uuid, 0, n.name, n.source_type, n.url,
            n.summary AS content, n.tags, n.metadata,
            ROW_NUMBER() OVER (
                ORDER BY ts_rank_cd(n.fts, websearch_to_tsquery('english', p_query_text)) DESC
            ) AS rank
        FROM kg_nodes n
        WHERE n.user_id = p_user_id
          AND (p_effective_nodes IS NULL OR n.id = ANY(p_effective_nodes))
          AND p_query_text IS NOT NULL AND p_query_text <> ''
          AND n.fts @@ websearch_to_tsquery('english', p_query_text)
        ORDER BY ts_rank_cd(n.fts, websearch_to_tsquery('english', p_query_text)) DESC
        LIMIT p_limit * 3
    ),
    fts_chunk AS (
        SELECT
            'chunk'::text                                         AS kind,
            c.node_id, c.id, c.chunk_idx, n.name, n.source_type, n.url,
            c.content, n.tags, c.metadata,
            ROW_NUMBER() OVER (
                ORDER BY ts_rank_cd(c.fts, websearch_to_tsquery('english', p_query_text)) DESC
            ) AS rank
        FROM kg_node_chunks c
        JOIN kg_nodes n ON n.user_id = p_user_id AND n.id = c.node_id
        WHERE c.user_id = p_user_id
          AND (p_effective_nodes IS NULL OR c.node_id = ANY(p_effective_nodes))
          AND p_query_text IS NOT NULL AND p_query_text <> ''
          AND c.fts @@ websearch_to_tsquery('english', p_query_text)
        ORDER BY ts_rank_cd(c.fts, websearch_to_tsquery('english', p_query_text)) DESC
        LIMIT p_limit * 3
    ),
    seeds AS (
        SELECT node_id, rank FROM dense_summary WHERE rank <= 5
    ),
    graph_walk AS (
        SELECT s.node_id AS nid, 0 AS depth, s.rank AS seed_rank, ARRAY[s.node_id] AS path
        FROM seeds s

        UNION ALL

        SELECT
            CASE WHEN l.source_node_id = w.nid THEN l.target_node_id ELSE l.source_node_id END AS nid,
            w.depth + 1 AS depth,
            w.seed_rank AS seed_rank,
            w.path || (CASE WHEN l.source_node_id = w.nid THEN l.target_node_id ELSE l.source_node_id END) AS path
        FROM graph_walk w
        JOIN kg_links l ON l.user_id = p_user_id
                       AND (l.source_node_id = w.nid OR l.target_node_id = w.nid)
        WHERE w.depth < p_graph_depth
          AND NOT ((CASE WHEN l.source_node_id = w.nid THEN l.target_node_id ELSE l.source_node_id END) = ANY(w.path))
    ),
    graph_expand AS (
        SELECT DISTINCT ON (w.nid)
            'summary'::text                                       AS kind,
            n2.id AS node_id,
            NULL::uuid AS chunk_id,
            0 AS chunk_idx,
            n2.name, n2.source_type, n2.url,
            n2.summary AS content, n2.tags, n2.metadata,
            w.seed_rank + w.depth                                  AS rank
        FROM graph_walk w
        JOIN kg_nodes n2 ON n2.user_id = p_user_id AND n2.id = w.nid
        WHERE w.depth > 0
          AND (p_effective_nodes IS NULL OR n2.id = ANY(p_effective_nodes))
    ),
    fused AS (
        SELECT kind, node_id, chunk_id, chunk_idx, name, source_type, url,
               content, tags, metadata,
               p_semantic_weight * 0.5 / (p_rrf_k + rank)::float AS score
        FROM dense_summary
        UNION ALL
        SELECT kind, node_id, chunk_id, chunk_idx, name, source_type, url,
               content, tags, metadata,
               p_semantic_weight * 0.5 / (p_rrf_k + rank)::float
        FROM dense_chunk
        UNION ALL
        SELECT kind, node_id, chunk_id, chunk_idx, name, source_type, url,
               content, tags, metadata,
               p_fulltext_weight * 0.5 / (p_rrf_k + rank)::float
        FROM fts_summary
        UNION ALL
        SELECT kind, node_id, chunk_id, chunk_idx, name, source_type, url,
               content, tags, metadata,
               p_fulltext_weight * 0.5 / (p_rrf_k + rank)::float
        FROM fts_chunk
        UNION ALL
        SELECT kind, node_id, chunk_id, chunk_idx, name, source_type, url,
               content, tags, metadata,
               p_graph_weight / (p_rrf_k + rank)::float
        FROM graph_expand
    ),
    aggregated AS (
        SELECT DISTINCT ON (kind, node_id, chunk_id)
            kind, node_id, chunk_id, chunk_idx,
            name, source_type, url, content, tags, metadata,
            SUM(score) OVER (PARTITION BY kind, node_id, chunk_id) AS rrf_score
        FROM fused
        ORDER BY kind, node_id, chunk_id, score DESC
    )
    SELECT
        a.kind, a.node_id, a.chunk_id, a.chunk_idx,
        a.name, a.source_type, a.url, a.content, a.tags, a.metadata, a.rrf_score
    FROM aggregated a
    ORDER BY a.rrf_score DESC
    LIMIT p_limit;
END;
$$;
