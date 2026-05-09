-- Phase 1.D.2: Hybrid (dense + FTS + RRF) search RPC.
-- Per Q1.D.2 approval (Supabase official match_hybrid pattern, Cormack 2009 RRF,
-- Tiger Data benchmark 62%->84% precision, Jonathan Katz pgvector hybrid).
-- Body: two CTEs (FTS via websearch_to_tsquery + ts_rank_cd, semantic via halfvec
-- cosine), FULL OUTER JOIN, RRF score = sum(1/(k+rank) * weight) per source.
-- RRF is rank-only per Cormack 2009 — never mix raw scores in fusion.
-- The fts column on content.canonical_chunks is GIN-indexed in 02_content_schema.sql
-- (idx_canonical_chunks_fts) — no extra index needed here.

CREATE OR REPLACE FUNCTION content.hybrid_search_chunks(
    p_workspace_id        uuid,
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
    title                text,
    source_type          text,
    publication_date     date,
    user_tags            text[],
    workspace_zettel_id  uuid
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())
            OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role') THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        WITH ft AS (
            -- FTS CTE: tenant predicate INSIDE — never push to outer query
            SELECT cc.id AS chunk_id,
                   row_number() OVER (
                       ORDER BY ts_rank_cd(cc.fts, websearch_to_tsquery('english', p_query_text)) DESC
                   ) AS rank
              FROM content.canonical_chunks cc
              JOIN content.workspace_chunk_membership wcm
                ON wcm.canonical_chunk_id = cc.id
             WHERE wcm.workspace_id = p_workspace_id
               AND cc.fts @@ websearch_to_tsquery('english', p_query_text)
             ORDER BY rank
             LIMIT p_match_count * 2
        ),
        sem AS (
            -- Semantic CTE: tenant predicate INSIDE
            SELECT cc.id AS chunk_id,
                   row_number() OVER (
                       ORDER BY cc.embedding <=> p_query_embedding ASC
                   ) AS rank
              FROM content.canonical_chunks cc
              JOIN content.workspace_chunk_membership wcm
                ON wcm.canonical_chunk_id = cc.id
             WHERE wcm.workspace_id = p_workspace_id
               AND cc.embedding IS NOT NULL
             ORDER BY cc.embedding <=> p_query_embedding ASC
             LIMIT p_match_count * 2
        ),
        fused AS (
            SELECT COALESCE(ft.chunk_id, sem.chunk_id) AS chunk_id,
                   COALESCE(1.0 / (p_rrf_k + ft.rank), 0.0) * p_full_text_weight
                 + COALESCE(1.0 / (p_rrf_k + sem.rank), 0.0) * p_semantic_weight
                       AS rrf_score,
                   ft.rank::int  AS fts_rank,
                   sem.rank::int AS semantic_rank
              FROM ft
              FULL OUTER JOIN sem ON ft.chunk_id = sem.chunk_id
        )
        SELECT cc.id              AS canonical_chunk_id,
               cc.canonical_zettel_id,
               cc.chunk_idx,
               cc.content,
               fused.rrf_score,
               fused.fts_rank,
               fused.semantic_rank,
               cz.title,
               cz.source_type,
               cz.publication_date,
               wz.user_tags,
               wz.id              AS workspace_zettel_id
          FROM fused
          JOIN content.canonical_chunks cc      ON cc.id = fused.chunk_id
          JOIN content.workspace_chunk_membership wcm
                                               ON wcm.canonical_chunk_id = cc.id
                                              AND wcm.workspace_id = p_workspace_id
          JOIN content.workspace_zettels wz    ON wz.id = wcm.workspace_zettel_id
          JOIN content.canonical_zettels cz    ON cz.id = cc.canonical_zettel_id
         WHERE wz.deleted_at IS NULL
         ORDER BY fused.rrf_score DESC
         LIMIT p_match_count;
END $$;
GRANT EXECUTE ON FUNCTION content.hybrid_search_chunks(uuid, text, halfvec, int, int, double precision, double precision)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
