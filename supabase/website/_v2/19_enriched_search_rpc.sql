-- Phase 1.D.1: Enriched chunk-search RPC.
-- Per Q1.D.1 approval (Pinecone include_metadata, Vespa phased ranking,
-- Elasticsearch _source, Weaviate _additional, Supabase pgvector convention).
-- The slim content.search_chunks RPC (already in v2 schema) stays for callers
-- that don't need metadata. This RPC adds the JOIN to canonical_zettels +
-- workspace_zettels overlay so callers get title, source_type, publication_date,
-- user_tags, fts_text in one round-trip.

CREATE OR REPLACE FUNCTION content.search_chunks_enriched(
    p_workspace_id     uuid,
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
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())
            OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role') THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT cc.id              AS canonical_chunk_id,
               cc.canonical_zettel_id,
               cc.chunk_idx,
               cc.content,
               (1 - (cc.embedding <=> p_query_embedding))::double precision AS score,
               cz.title,
               cz.source_type,
               cz.publication_date,
               wz.user_tags,
               wz.id              AS workspace_zettel_id,
               cc.content         AS fts_text  -- caller may use for highlighting
          FROM content.canonical_chunks cc
          JOIN content.workspace_chunk_membership wcm
            ON wcm.canonical_chunk_id = cc.id
          JOIN content.workspace_zettels wz
            ON wz.id = wcm.workspace_zettel_id
          JOIN content.canonical_zettels cz
            ON cz.id = cc.canonical_zettel_id
         WHERE wcm.workspace_id = p_workspace_id
           AND wz.deleted_at IS NULL
           AND cc.embedding IS NOT NULL
         ORDER BY cc.embedding <=> p_query_embedding ASC
         LIMIT p_match_count;
END $$;
GRANT EXECUTE ON FUNCTION content.search_chunks_enriched(uuid, halfvec, int)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
