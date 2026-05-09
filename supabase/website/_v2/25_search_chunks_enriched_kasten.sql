-- Phase 1.D.7: Kasten-scoped enriched chunk-search RPC.
-- Per Q#2 approval (Pinecone namespaces, Weaviate ACORN 1.34, pgvector 0.8 iterative-scan).
-- Push kasten JOIN into earliest CTE so HNSW + GIN see only the kasten subset.
-- At ~0.05-0.5% selectivity (kasten=5-50 of 10k+ zettels), pre-filter dominates post-filter.

CREATE OR REPLACE FUNCTION content.search_chunks_enriched_kasten(
    p_kasten_id        uuid,
    p_query_embedding  halfvec(768),
    p_match_count      int DEFAULT 20
) RETURNS TABLE (
    canonical_chunk_id   uuid,
    canonical_zettel_id  uuid,
    chunk_idx            int,
    content              text,
    score                double precision,
    title                text,
    source_type          text,
    publication_date     date,
    user_tags            text[],
    workspace_zettel_id  uuid,
    fts_text             text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    -- Authorise via kasten ownership.
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        WITH scoped_chunks AS (
            -- Narrow scope FIRST (kasten -> workspace_zettels -> chunks)
            -- before HNSW/GIN see the data. ACORN/iterative-scan pattern.
            -- Column aliases avoid ambiguity with OUT parameter names below.
            SELECT cc.id              AS sc_chunk_id,
                   cc.canonical_zettel_id AS sc_zettel_id,
                   cc.chunk_idx       AS sc_chunk_idx,
                   cc.content         AS sc_content,
                   cc.embedding       AS sc_embedding,
                   wz.id              AS sc_wz_id,
                   wz.user_tags       AS sc_user_tags,
                   cz.title           AS sc_title,
                   cz.source_type     AS sc_source_type,
                   cz.publication_date AS sc_pub_date
              FROM rag.kasten_zettels kz
              JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
              JOIN content.workspace_chunk_membership wcm
                ON wcm.workspace_zettel_id = wz.id
              JOIN content.canonical_chunks cc ON cc.id = wcm.canonical_chunk_id
              JOIN content.canonical_zettels cz ON cz.id = cc.canonical_zettel_id
             WHERE kz.kasten_id = p_kasten_id
               AND wz.deleted_at IS NULL
               AND cc.embedding IS NOT NULL
        )
        SELECT sc.sc_chunk_id, sc.sc_zettel_id, sc.sc_chunk_idx, sc.sc_content,
               (1 - (sc.sc_embedding <=> p_query_embedding))::double precision AS score,
               sc.sc_title, sc.sc_source_type, sc.sc_pub_date, sc.sc_user_tags,
               sc.sc_wz_id, sc.sc_content AS fts_text
          FROM scoped_chunks sc
         ORDER BY sc.sc_embedding <=> p_query_embedding ASC
         LIMIT p_match_count;
END $$;
GRANT EXECUTE ON FUNCTION content.search_chunks_enriched_kasten(uuid, halfvec, int)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
