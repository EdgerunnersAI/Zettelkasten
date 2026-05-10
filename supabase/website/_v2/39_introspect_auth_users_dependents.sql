-- Phase 8.5.R2-1 — SECURITY DEFINER RPC: discover all FKs to auth.users
-- recursively. Used by tests/integration/v2/test_user_cascade.py to drive
-- a tag-and-sweep regression test that asserts CASCADE actually propagates
-- across the v2 chain (core.profiles → core.workspaces → workspace_members
-- → content.workspace_zettels → content.workspace_chunk_membership →
-- kg.kg_nodes/kg_edges → pipelines.pipeline_runs → rag.kastens/chat_*/
-- kasten_zettels/kasten_members → billing.pricing_subscriptions →
-- rag.retrieval_feedback_events).
--
-- Why SECURITY DEFINER: information_schema.referential_constraints + pg_catalog
-- reads over the auth schema require elevated reads supabase-py can't do
-- via REST otherwise. EXECUTE granted only to service_role.
--
-- Pattern: AWS Database Blog "Managing object dependencies in PostgreSQL"
-- + arXiv 2510.26284 hierarchical-multi-bandit references converge on
-- this pg_depend / referential_constraints recursive walk.

CREATE OR REPLACE FUNCTION core.introspect_auth_users_dependents()
RETURNS TABLE(schema_name text, table_name text, fk_column text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    WITH RECURSIVE
    -- All FK constraints with their (src schema, src table, src column,
    -- ref schema, ref table) tuples directly from pg_constraint.
    -- pg_constraint sees auth-schema FKs that information_schema hides
    -- behind permission checks for non-superuser roles.
    fk_edges AS (
        SELECT
            srcn.nspname::text AS src_schema,
            srcc.relname::text AS src_table,
            srca.attname::text AS src_column,
            refn.nspname::text AS ref_schema,
            refc.relname::text AS ref_table
          FROM pg_constraint con
          JOIN pg_class       srcc ON srcc.oid = con.conrelid
          JOIN pg_namespace   srcn ON srcn.oid = srcc.relnamespace
          JOIN pg_class       refc ON refc.oid = con.confrelid
          JOIN pg_namespace   refn ON refn.oid = refc.relnamespace
          JOIN pg_attribute   srca
            ON srca.attrelid = srcc.oid
           AND srca.attnum   = con.conkey[1]   -- first FK column
         WHERE con.contype = 'f'
    ),
    deps AS (
        -- Direct FKs to auth.users
        SELECT src_schema, src_table, src_column
          FROM fk_edges
         WHERE ref_schema = 'auth' AND ref_table = 'users'
        UNION
        -- Transitive FKs to anything we've already discovered
        SELECT e.src_schema, e.src_table, e.src_column
          FROM fk_edges e
          JOIN deps d
            ON d.src_schema = e.ref_schema
           AND d.src_table  = e.ref_table
    )
    SELECT DISTINCT
        src_schema AS schema_name,
        src_table  AS table_name,
        src_column AS fk_column
      FROM deps
     ORDER BY schema_name, table_name;
$$;

REVOKE ALL ON FUNCTION core.introspect_auth_users_dependents() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.introspect_auth_users_dependents() TO service_role;

COMMENT ON FUNCTION core.introspect_auth_users_dependents() IS
    'Phase 8.5.R2-1: returns (schema, table, fk_column) for every table that '
    'transitively FKs to auth.users. Used by CASCADE chain regression test. '
    'Auto-extends as new tables join the chain — no test maintenance needed.';

NOTIFY pgrst, 'reload schema';
