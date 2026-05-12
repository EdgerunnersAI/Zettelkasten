-- WAVE-C D-ZOMBIE-b: port match_kg_nodes RPC to v2 schema.
--
-- Background:
--   * Phase 8 closeout (2026-05-11) dropped `public.kg_*` tables but the
--     original `public.match_kg_nodes(query_embedding vector(768), …,
--     target_user_id uuid)` body still referenced `public.kg_nodes`. As a
--     result every call from `find_similar_nodes` raised a PostgREST error
--     which the Python wrapper swallowed → silent `[]`.
--   * v2 stores per-user KG nodes in `kg.kg_nodes` (workspace-scoped via
--     `kg.kg_nodes.workspace_id`) and embeddings on `content.canonical_chunks`
--     (`embedding halfvec(768)`). The chunk↔node link is
--     `kg.chunk_node_mentions(canonical_chunk_id, kg_node_id, mention_type)`
--     — there is NO `canonical_chunk_id` column on `kg.kg_nodes` itself.
--   * `embedding_model_version text NOT NULL` on canonical_chunks must be
--     filtered server-side so cross-version cosine collisions cannot leak
--     between embedding generations (KF-EMB-B contract).
--
-- This migration:
--   1. DROPs the orphan `public.match_kg_nodes` (every legacy overload).
--   2. CREATEs `kg.match_kg_nodes(p_user_id, p_query_embedding,
--      p_model_version, p_match_threshold, p_match_count)` returning
--      `(node_id bigint, score real)`.
--   3. Resolves `p_user_id` (profile/auth uid) to that user's personal
--      workspace via `core.workspaces.owner_profile_id` so the wrapper API
--      stays user-centric while the storage layer remains workspace-scoped.
--   4. SECURITY DEFINER + service_role/authenticated grants matching peer
--      RPCs in `kg.expand_subgraph` / `content.search_chunks`.
--
-- Anti-pattern guards:
--   * `billing.pricing_consume_entitlement` is NOT touched (golden-SHA256).
--   * Existing migrations are NOT altered; this is purely additive.
--   * No legacy `public.kg_*` table is referenced.
--
-- Verification:
--   * `tests/integration/v2/test_kg_embedding_provenance.py` flips from
--     "RPC lacks p_model_version" to "RPC has p_model_version" + positive
--     coverage that filtering by version excludes other-version chunks.

-- ── 1. DROP every legacy overload of public.match_kg_nodes ──────────────────
-- A loop over pg_proc lets us drop *all* overloads regardless of the historical
-- argument list (the legacy signature took (vector, float, int, uuid) but
-- earlier iterations may have shipped variants).
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT n.nspname AS schema_name,
               p.proname AS func_name,
               pg_get_function_identity_arguments(p.oid) AS args
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE p.proname = 'match_kg_nodes'
           AND n.nspname = 'public'
    LOOP
        EXECUTE format(
            'DROP FUNCTION IF EXISTS %I.%I(%s) CASCADE',
            r.schema_name, r.func_name, r.args
        );
    END LOOP;
END
$$;

-- ── 2. v2 RPC in kg schema ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION kg.match_kg_nodes(
    p_user_id          uuid,
    p_query_embedding  halfvec(768),
    p_model_version    text,
    p_match_threshold  real DEFAULT 0.75,
    p_match_count      int  DEFAULT 10
) RETURNS TABLE (
    node_id  bigint,
    score    real
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_workspace_ids uuid[];
BEGIN
    -- Resolve the calling user to their owned workspaces. SECURITY DEFINER
    -- means we cannot rely on RLS — the explicit owner_profile_id filter is
    -- the authz fence. Service-role callers (server-side ingest) skip the
    -- fence and may probe any user_id; authenticated callers must match
    -- p_user_id against their own JWT subject.
    IF NOT core.is_service_role() THEN
        IF p_user_id IS NULL OR p_user_id <> auth.uid() THEN
            RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
        END IF;
    END IF;

    SELECT array_agg(w.id)
      INTO v_workspace_ids
      FROM core.workspaces w
     WHERE w.owner_profile_id = p_user_id;

    IF v_workspace_ids IS NULL OR array_length(v_workspace_ids, 1) = 0 THEN
        RETURN;
    END IF;

    -- HNSW iterative scan: keeps recall stable when we apply the
    -- model_version + workspace + threshold post-filters above the index.
    PERFORM set_config('hnsw.iterative_scan', 'relaxed_order', true);

    RETURN QUERY
        SELECT n.id AS node_id,
               (1 - (cc.embedding <=> p_query_embedding))::real AS score
          FROM kg.kg_nodes n
          JOIN kg.chunk_node_mentions m ON m.kg_node_id = n.id
          JOIN content.canonical_chunks cc ON cc.id = m.canonical_chunk_id
         WHERE n.workspace_id = ANY (v_workspace_ids)
           AND cc.embedding IS NOT NULL
           AND cc.embedding_model_version = p_model_version
           AND (1 - (cc.embedding <=> p_query_embedding)) > p_match_threshold
         ORDER BY cc.embedding <=> p_query_embedding
         LIMIT p_match_count;
END
$$;

REVOKE ALL ON FUNCTION kg.match_kg_nodes(uuid, halfvec, text, real, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.match_kg_nodes(uuid, halfvec, text, real, int)
    TO authenticated, service_role;

COMMENT ON FUNCTION kg.match_kg_nodes(uuid, halfvec, text, real, int) IS
    'WAVE-C: port of legacy public.match_kg_nodes to v2. Filters by '
    'embedding_model_version to prevent cross-version cosine collisions.';
