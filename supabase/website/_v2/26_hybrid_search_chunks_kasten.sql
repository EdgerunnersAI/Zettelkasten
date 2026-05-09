-- Phase 1.D.8: Kasten-scoped hybrid search (dense + FTS + RRF).
-- Per Q#2 approval. Kasten predicate INSIDE both CTEs so HNSW + GIN apply on the
-- narrowed set. RRF rank-only per Cormack 2009.

CREATE OR REPLACE FUNCTION content.hybrid_search_chunks_kasten(
    p_kasten_id           uuid,
    p_query_text          text,
    p_query_embedding     halfvec(768),
    p_match_count         int DEFAULT 20,
    p_rrf_k               int DEFAULT 60,
    p_full_text_weight    double precision DEFAULT 1.0,
    p_semantic_weight     double precision DEFAULT 1.0
) RETURNS TABLE (
    canonical_chunk_id   uuid,
    canonical_zettel_id  uuid,
    chunk_idx            int,
    content              text,
    rrf_score            double precision,
    fts_rank             int,
    semantic_rank        int,
    raw_dense_score      double precision,
    raw_fts_score        double precision,
    title                text,
    source_type          text,
    publication_date     date,
    user_tags            text[],
    workspace_zettel_id  uuid
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
            -- Pre-filter to kasten chunks; HNSW + GIN see only this subset.
            -- Column aliases avoid ambiguity with OUT parameter names below.
            SELECT cc.id              AS sc_chunk_id,
                   cc.canonical_zettel_id AS sc_zettel_id,
                   cc.chunk_idx       AS sc_chunk_idx,
                   cc.content         AS sc_content,
                   cc.embedding       AS sc_embedding,
                   cc.fts             AS sc_fts,
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
        ),
        ft AS (
            SELECT sc_chunk_id AS chunk_id,
                   row_number() OVER (
                       ORDER BY ts_rank_cd(sc_fts, websearch_to_tsquery('english', p_query_text)) DESC
                   ) AS rank,
                   ts_rank_cd(sc_fts, websearch_to_tsquery('english', p_query_text))::double precision AS raw_fts
              FROM scoped_chunks
             WHERE sc_fts @@ websearch_to_tsquery('english', p_query_text)
             ORDER BY rank
             LIMIT p_match_count * 2
        ),
        sem AS (
            SELECT sc_chunk_id AS chunk_id,
                   row_number() OVER (
                       ORDER BY sc_embedding <=> p_query_embedding ASC
                   ) AS rank,
                   (1 - (sc_embedding <=> p_query_embedding))::double precision AS raw_cosine
              FROM scoped_chunks
             WHERE sc_embedding IS NOT NULL
             ORDER BY sc_embedding <=> p_query_embedding ASC
             LIMIT p_match_count * 2
        ),
        fused AS (
            SELECT COALESCE(ft.chunk_id, sem.chunk_id) AS chunk_id,
                   COALESCE(1.0 / (p_rrf_k + ft.rank), 0.0) * p_full_text_weight
                 + COALESCE(1.0 / (p_rrf_k + sem.rank), 0.0) * p_semantic_weight AS f_rrf_score,
                   ft.rank::int AS f_fts_rank,
                   sem.rank::int AS f_sem_rank,
                   sem.raw_cosine AS f_raw_dense,
                   ft.raw_fts    AS f_raw_fts
              FROM ft FULL OUTER JOIN sem ON ft.chunk_id = sem.chunk_id
        )
        SELECT sc.sc_chunk_id  AS canonical_chunk_id,
               sc.sc_zettel_id AS canonical_zettel_id,
               sc.sc_chunk_idx AS chunk_idx,
               sc.sc_content   AS content,
               fused.f_rrf_score AS rrf_score,
               fused.f_fts_rank  AS fts_rank,
               fused.f_sem_rank  AS semantic_rank,
               fused.f_raw_dense AS raw_dense_score,
               fused.f_raw_fts   AS raw_fts_score,
               sc.sc_title       AS title,
               sc.sc_source_type AS source_type,
               sc.sc_pub_date    AS publication_date,
               sc.sc_user_tags   AS user_tags,
               sc.sc_wz_id       AS workspace_zettel_id
          FROM fused
          JOIN scoped_chunks sc ON sc.sc_chunk_id = fused.chunk_id
         ORDER BY fused.f_rrf_score DESC
         LIMIT p_match_count;
END $$;
GRANT EXECUTE ON FUNCTION content.hybrid_search_chunks_kasten(uuid, text, halfvec, int, int, double precision, double precision)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
