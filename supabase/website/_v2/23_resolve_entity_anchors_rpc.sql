-- Phase 1.D.5: Resolve entity anchors RPC.
-- Maps free-text query terms to kg_node ids using canonical names + aliases
-- via pg_trgm similarity. Tenant scoped on workspace_id (deny by default).
-- Internal column names use a `_v` suffix to avoid collisions with OUT params.

CREATE OR REPLACE FUNCTION kg.resolve_entity_anchors_v2(
    p_workspace_id  uuid,
    p_terms         text[],
    p_min_similarity double precision DEFAULT 0.30
) RETURNS TABLE (
    kg_node_id     bigint,
    canonical_name text,
    matched_alias  text,
    matched_kind   text,
    similarity     double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())
            OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role') THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    IF p_terms IS NULL OR cardinality(p_terms) = 0 THEN
        RETURN;
    END IF;
    RETURN QUERY
        WITH terms AS (
            SELECT DISTINCT lower(t) AS term FROM unnest(p_terms) t
        ),
        canonical_matches AS (
            SELECT n.id AS kg_node_id_v,
                   n.canonical_name AS canonical_name_v,
                   n.canonical_name AS matched_alias_v,
                   'canonical'::text AS matched_kind_v,
                   public.similarity(lower(n.canonical_name), terms.term) AS sim
              FROM kg.kg_nodes n CROSS JOIN terms
             WHERE n.workspace_id = p_workspace_id
               AND lower(n.canonical_name) % terms.term
        ),
        alias_matches AS (
            SELECT a.kg_node_id AS kg_node_id_v,
                   n.canonical_name AS canonical_name_v,
                   a.alias AS matched_alias_v,
                   a.alias_kind AS matched_kind_v,
                   public.similarity(lower(a.alias), terms.term) AS sim
              FROM kg.kg_node_aliases a
              JOIN kg.kg_nodes n ON n.id = a.kg_node_id
              CROSS JOIN terms
             WHERE n.workspace_id = p_workspace_id
               AND lower(a.alias) % terms.term
        ),
        all_matches AS (
            SELECT * FROM canonical_matches
            UNION ALL
            SELECT * FROM alias_matches
        ),
        ranked AS (
            SELECT am.kg_node_id_v,
                   am.canonical_name_v,
                   am.matched_alias_v,
                   am.matched_kind_v,
                   am.sim,
                   row_number() OVER (
                       PARTITION BY am.kg_node_id_v
                       ORDER BY am.sim DESC, am.matched_kind_v ASC
                   ) AS rn
              FROM all_matches am
             WHERE am.sim >= p_min_similarity
        )
        SELECT r.kg_node_id_v,
               r.canonical_name_v,
               r.matched_alias_v,
               r.matched_kind_v,
               r.sim::double precision
          FROM ranked r
         WHERE r.rn = 1
         ORDER BY r.sim DESC;
END $$;
GRANT EXECUTE ON FUNCTION kg.resolve_entity_anchors_v2(uuid, text[], double precision)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
