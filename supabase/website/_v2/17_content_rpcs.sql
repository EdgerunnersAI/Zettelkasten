-- supabase/website/_v2/17_content_rpcs.sql
-- Phase 1.C: SECURITY DEFINER RPCs for the content schema.
-- Used by ContentRepository.upsert_canonical_zettel (Phase-2.0 prereq for Phase 4).

CREATE OR REPLACE FUNCTION content.upsert_canonical_zettel(
    p_normalized_url   text,
    p_content_hash     bytea,
    p_source_type      text,
    p_title            text,
    p_body_md          text,
    p_publication_date date,
    p_source_metadata  jsonb
) RETURNS TABLE (id uuid, was_new boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    -- Round-2 R2.6: ON CONFLICT DO UPDATE SET normalized_url = EXCLUDED.normalized_url
    -- (literal no-op self-assign) — DO NOTHING would return zero rows on conflict and
    -- break the (xmax = 0) was-new detection. The self-assign returns the row from
    -- RETURNING and xmax correctly identifies the inserter under concurrent contention.
    RETURN QUERY
        INSERT INTO content.canonical_zettels (
            normalized_url, content_hash, source_type, title,
            body_md, publication_date, source_metadata
        )
        VALUES (
            p_normalized_url, p_content_hash, p_source_type, p_title,
            p_body_md, p_publication_date, p_source_metadata
        )
        ON CONFLICT (normalized_url, content_hash)
        DO UPDATE SET normalized_url = EXCLUDED.normalized_url
        RETURNING canonical_zettels.id, (xmax = 0) AS was_new;
END $$;

GRANT EXECUTE ON FUNCTION content.upsert_canonical_zettel(text, bytea, text, text, text, date, jsonb)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
