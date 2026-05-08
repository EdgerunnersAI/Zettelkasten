-- ============================================================================
-- 005_rag_rpcs.sql
-- 1. rag_resolve_effective_nodes  — compose sandbox + scope filter → node_id set
-- 2. rag_hybrid_search             — 5-stream RRF retrieval
-- 3. rag_subgraph_for_pagerank     — induced subgraph for NetworkX scoring
-- 4. rag_bulk_add_to_sandbox       — server-side bulk membership add
-- 5. rag_replace_node_chunks       — atomic delete for chunk re-ingest
-- ============================================================================

-- ── 1. rag_resolve_effective_nodes ────────────────────────────────────────
CREATE OR REPLACE FUNCTION rag_resolve_effective_nodes(
    p_user_id      uuid,
    p_sandbox_id   uuid    DEFAULT NULL,
    p_node_ids     text[]  DEFAULT NULL,
    p_tags         text[]  DEFAULT NULL,
    p_tag_mode     text    DEFAULT 'all',     -- 'all' = @>, 'any' = &&
    p_source_types text[]  DEFAULT NULL
)
RETURNS TABLE (node_id text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = ''
SET statement_timeout = '3s'
AS $$
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

COMMENT ON FUNCTION rag_resolve_effective_nodes IS
    'Composes sandbox membership + scope filters into the effective node set for retrieval';


-- ── 2. rag_hybrid_search ──────────────────────────────────────────────────
-- p_effective_nodes: nullable. NULL = "all user's nodes" (fast path for ad-hoc
-- queries or large sandboxes) — avoids marshaling huge arrays.
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
    p_recency_decay    float   DEFAULT 0.0    -- γ from BP3 formula; 0 = disabled in v1
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
BEGIN
    -- Per-session knobs: HNSW recall + iterative scan for multi-tenant correctness
    PERFORM set_config('hnsw.ef_search',       '100',          true);
    PERFORM set_config('hnsw.iterative_scan',  'strict_order', true);
    PERFORM set_config('hnsw.max_scan_tuples', '20000',        true);

    RETURN QUERY
    WITH
    -- ── Stream 1a: Dense over summary embeddings (kg_nodes) ────────────────
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
    -- ── Stream 1b: Dense over chunk embeddings (kg_node_chunks) ────────────
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
    -- ── Stream 2a: FTS over summaries ──────────────────────────────────────
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
    -- ── Stream 2b: FTS over chunks ─────────────────────────────────────────
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
    -- ── Stream 3: Graph expansion from MMR-diversified dense_summary seeds ──
    --    Recursive CTE bounded by p_graph_depth (1 for lookup, 2 for thematic).
    --    Must stay within effective node set.
    --
    --    iter-04 MMR: previously SELECT WHERE rank <= 5 took the top-5
    --    by raw dense rank, which let a topic-magnet node both *seed* the
    --    graph walk *and* receive re-injection through its own neighbours
    --    — the q5-class self-seeding loop. We now pick the best seed per
    --    source_type (1 per youtube/github/web/...) before backfilling
    --    by overall rank, so seeds are diverse-by-construction. No DPP /
    --    learned diversification needed at this candidate-set size.
    seeds_ranked AS (
        SELECT ds.node_id,
               ds.rank,
               n.source_type,
               ROW_NUMBER() OVER (
                   PARTITION BY n.source_type
                   ORDER BY ds.rank
               ) AS source_type_rank,
               ROW_NUMBER() OVER (ORDER BY ds.rank) AS overall_rank
        FROM dense_summary ds
        LEFT JOIN kg_nodes n
            ON n.user_id = p_user_id AND n.id = ds.node_id
    ),
    seeds AS (
        -- Take top-1 per source type (diversity tier).
        SELECT node_id, rank
        FROM seeds_ranked
        WHERE source_type_rank = 1
        UNION
        -- Backfill remaining slots up to 5 by overall rank, skipping
        -- already-included node_ids (diversity tier may have <5 entries
        -- when the Kasten has few source types).
        SELECT node_id, rank
        FROM seeds_ranked
        WHERE overall_rank <= 5
          AND node_id NOT IN (
              SELECT node_id FROM seeds_ranked WHERE source_type_rank = 1
          )
        LIMIT 5
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
    -- ── RRF fusion across all 5 streams ────────────────────────────────────
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

COMMENT ON FUNCTION rag_hybrid_search IS
    '5-stream RRF: dense(summary) + dense(chunks) + fts(summary) + fts(chunks) + graph expansion. Returns top-N candidates.';


-- ── 3. rag_subgraph_for_pagerank ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION rag_subgraph_for_pagerank(
    p_user_id    uuid,
    p_node_ids   text[]
)
RETURNS TABLE (source_node_id text, target_node_id text, weight int)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
SET statement_timeout = '2s'
AS $$
    SELECT l.source_node_id, l.target_node_id, COALESCE(l.weight, 5) AS weight
    FROM public.kg_links l
    WHERE l.user_id = p_user_id
      AND l.source_node_id = ANY(p_node_ids)
      AND l.target_node_id = ANY(p_node_ids);
$$;


-- ── 4. rag_bulk_add_to_sandbox ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rag_bulk_add_to_sandbox(
    p_user_id      uuid,
    p_sandbox_id   uuid,
    p_tags         text[] DEFAULT NULL,
    p_tag_mode     text   DEFAULT 'all',
    p_source_types text[] DEFAULT NULL,
    p_node_ids     text[] DEFAULT NULL,
    p_added_via    text   DEFAULT 'bulk_tag'
) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = ''
SET statement_timeout = '10s'
AS $$
DECLARE
    n_added int;
BEGIN
    WITH candidates AS (
        SELECT id FROM public.kg_nodes
        WHERE user_id = p_user_id
          AND (p_node_ids     IS NULL OR id          = ANY(p_node_ids))
          AND (p_tags         IS NULL OR (
               (p_tag_mode = 'all' AND tags @> p_tags) OR
               (p_tag_mode = 'any' AND tags && p_tags)
               ))
          AND (p_source_types IS NULL OR source_type = ANY(p_source_types))
    ),
    inserted AS (
        INSERT INTO public.rag_sandbox_members (sandbox_id, user_id, node_id, added_via, added_filter)
        SELECT p_sandbox_id, p_user_id, c.id, p_added_via,
               jsonb_build_object('tags', p_tags, 'tag_mode', p_tag_mode, 'source_types', p_source_types)
        FROM candidates c
        ON CONFLICT (sandbox_id, node_id) DO NOTHING
        RETURNING 1
    )
    SELECT COUNT(*) INTO n_added FROM inserted;

    -- Touch last_used_at on the sandbox
    UPDATE public.rag_sandboxes
       SET last_used_at = now(), updated_at = now()
     WHERE id = p_sandbox_id AND user_id = p_user_id;

    RETURN n_added;
END;
$$;


-- ── 5. rag_replace_node_chunks ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rag_replace_node_chunks(
    p_user_id uuid,
    p_node_id text
) RETURNS void
LANGUAGE sql SECURITY DEFINER
SET search_path = ''
AS $$
    DELETE FROM public.kg_node_chunks
     WHERE user_id = p_user_id AND node_id = p_node_id;
$$;


-- Permissions
REVOKE ALL ON FUNCTION rag_resolve_effective_nodes  FROM PUBLIC;
REVOKE ALL ON FUNCTION rag_hybrid_search            FROM PUBLIC;
REVOKE ALL ON FUNCTION rag_subgraph_for_pagerank    FROM PUBLIC;
REVOKE ALL ON FUNCTION rag_bulk_add_to_sandbox      FROM PUBLIC;
REVOKE ALL ON FUNCTION rag_replace_node_chunks      FROM PUBLIC;

GRANT EXECUTE ON FUNCTION rag_resolve_effective_nodes TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION rag_hybrid_search           TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION rag_subgraph_for_pagerank   TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION rag_bulk_add_to_sandbox     TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION rag_replace_node_chunks     TO authenticated, service_role;
