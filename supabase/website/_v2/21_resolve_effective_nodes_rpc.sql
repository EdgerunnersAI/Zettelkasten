-- Phase 1.D.3: Scope-filtered effective-nodes RPC for kasten chat.
-- Replaces legacy rag_resolve_effective_nodes. Filters via array overlap operators
-- on workspace_zettels.user_tags (already GIN-indexed per Phase 1.A).

CREATE OR REPLACE FUNCTION rag.resolve_effective_nodes_v2(
    p_kasten_id      uuid,
    p_tags           text[]   DEFAULT NULL,
    p_source_types   text[]   DEFAULT NULL,
    p_tag_mode       text     DEFAULT 'any'
) RETURNS TABLE (
    workspace_zettel_id  uuid,
    canonical_zettel_id  uuid
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    -- Authorise via kasten ownership (NOT a passed-in workspace_id).
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    -- Validate p_tag_mode early
    IF p_tag_mode IS NOT NULL AND p_tag_mode NOT IN ('any', 'all', 'none') THEN
        RAISE EXCEPTION 'invalid tag_mode: %', p_tag_mode USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
        SELECT wz.id AS workspace_zettel_id,
               cz.id AS canonical_zettel_id
          FROM rag.kasten_zettels kz
          JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
          JOIN content.canonical_zettels cz ON cz.id = wz.canonical_zettel_id
         WHERE kz.kasten_id = p_kasten_id
           AND wz.deleted_at IS NULL
           AND (
               p_tags IS NULL OR cardinality(p_tags) = 0 OR
               (p_tag_mode = 'any'  AND wz.user_tags && p_tags) OR
               (p_tag_mode = 'all'  AND wz.user_tags @> p_tags) OR
               (p_tag_mode = 'none' AND NOT (wz.user_tags && p_tags))
           )
           AND (
               p_source_types IS NULL OR cardinality(p_source_types) = 0
               OR cz.source_type = ANY (p_source_types)
           );
END $$;
GRANT EXECUTE ON FUNCTION rag.resolve_effective_nodes_v2(uuid, text[], text[], text)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
