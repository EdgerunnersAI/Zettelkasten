-- ============================================================================
-- Scale + Storage Optimization Migration (003)
-- Targets: existing deployments that already ran 001/002 and Nexus schema
-- Goals:
--   1) Reduce index storage overhead without hurting user-facing queries
--   2) Improve hot-path query plans for Nexus per-user run views
--   3) Keep table bloat controlled for growing datasets
--   4) Add optional housekeeping function for short-lived OAuth/run data
-- ============================================================================

-- 1) Drop heavyweight / low-value global indexes --------------------------------
-- These can become expensive in storage and write amplification relative to
-- current query patterns.
DROP INDEX IF EXISTS public.idx_kg_nodes_global_cover;
DROP INDEX IF EXISTS public.idx_kg_nodes_url_global;


-- 2) Ensure source_type check includes twitter ----------------------------------
ALTER TABLE IF EXISTS public.kg_nodes
    DROP CONSTRAINT IF EXISTS kg_nodes_source_type_check;

ALTER TABLE IF EXISTS public.kg_nodes
    ADD CONSTRAINT kg_nodes_source_type_check
    CHECK (source_type IN (
        'youtube', 'reddit', 'github', 'twitter', 'substack', 'medium', 'web', 'generic'
    )) NOT VALID;

ALTER TABLE IF EXISTS public.kg_nodes
    VALIDATE CONSTRAINT kg_nodes_source_type_check;

CREATE INDEX IF NOT EXISTS idx_kg_nodes_user_created_at
    ON public.kg_nodes (user_id, created_at DESC);


-- 3) Add / right-size Nexus indexes ---------------------------------------------
CREATE INDEX IF NOT EXISTS idx_nexus_ingest_runs_user_started
    ON public.nexus_ingest_runs (user_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_oauth_states_unconsumed_expires
    ON public.nexus_oauth_states (expires_at)
    WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_provider_accounts_expires_at
    ON public.nexus_provider_accounts (expires_at)
    WHERE expires_at IS NOT NULL;

DROP INDEX IF EXISTS public.idx_nexus_ingested_artifacts_ingest_run_legacy;
CREATE INDEX IF NOT EXISTS idx_nexus_ingested_artifacts_ingest_run_legacy
    ON public.nexus_ingested_artifacts (ingest_run_id)
    WHERE ingest_run_id IS NOT NULL;


-- 4) Refresh KG RLS predicates for better planner behavior ----------------------
-- Use (SELECT auth.uid()) and keyed EXISTS checks to reduce per-row overhead.
DROP POLICY IF EXISTS kg_users_select ON public.kg_users;
CREATE POLICY kg_users_select ON public.kg_users
    FOR SELECT USING (render_user_id = (SELECT auth.uid())::text);

DROP POLICY IF EXISTS kg_users_update ON public.kg_users;
CREATE POLICY kg_users_update ON public.kg_users
    FOR UPDATE USING (render_user_id = (SELECT auth.uid())::text);

DROP POLICY IF EXISTS kg_nodes_select ON public.kg_nodes;
CREATE POLICY kg_nodes_select ON public.kg_nodes
    FOR SELECT USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_nodes_insert ON public.kg_nodes;
CREATE POLICY kg_nodes_insert ON public.kg_nodes
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_nodes_update ON public.kg_nodes;
CREATE POLICY kg_nodes_update ON public.kg_nodes
    FOR UPDATE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_nodes_delete ON public.kg_nodes;
CREATE POLICY kg_nodes_delete ON public.kg_nodes
    FOR DELETE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_links_select ON public.kg_links;
CREATE POLICY kg_links_select ON public.kg_links
    FOR SELECT USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_links_insert ON public.kg_links;
CREATE POLICY kg_links_insert ON public.kg_links
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_links_update ON public.kg_links;
CREATE POLICY kg_links_update ON public.kg_links
    FOR UPDATE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_links_delete ON public.kg_links;
CREATE POLICY kg_links_delete ON public.kg_links
    FOR DELETE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );


-- 5) Autovacuum tuning for high-churn tables -----------------------------------
ALTER TABLE IF EXISTS public.kg_nodes SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

ALTER TABLE IF EXISTS public.kg_links SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

ALTER TABLE IF EXISTS public.nexus_oauth_states SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01
);

ALTER TABLE IF EXISTS public.nexus_ingest_runs SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

ALTER TABLE IF EXISTS public.nexus_ingested_artifacts SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);


-- 6) Optional housekeeping function ---------------------------------------------
-- Keeps short-lived tables small when scheduled (for example via pg_cron).
-- No data is deleted automatically by this migration.
CREATE OR REPLACE FUNCTION public.nexus_housekeeping(
    p_oauth_retention interval DEFAULT interval '1 day',
    p_run_retention interval DEFAULT interval '120 days'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'public'
AS $$
DECLARE
    v_oauth_deleted bigint := 0;
    v_runs_deleted bigint := 0;
BEGIN
    DELETE FROM public.nexus_oauth_states
    WHERE expires_at < now() - p_oauth_retention
       OR (consumed_at IS NOT NULL AND consumed_at < now() - p_oauth_retention);
    GET DIAGNOSTICS v_oauth_deleted = ROW_COUNT;

    DELETE FROM public.nexus_ingest_runs
    WHERE status IN ('completed', 'partial_success', 'failed', 'cancelled')
      AND COALESCE(completed_at, started_at) < now() - p_run_retention;
    GET DIAGNOSTICS v_runs_deleted = ROW_COUNT;

    RETURN jsonb_build_object(
        'oauth_states_deleted', v_oauth_deleted,
        'ingest_runs_deleted', v_runs_deleted
    );
END;
$$;

REVOKE ALL ON FUNCTION public.nexus_housekeeping(interval, interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.nexus_housekeeping(interval, interval) TO service_role;

COMMENT ON FUNCTION public.nexus_housekeeping(interval, interval) IS
    'Optional retention helper for Nexus OAuth states and old ingest runs.';
