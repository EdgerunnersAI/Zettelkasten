-- supabase/website/_v2/16_nexus_tokens.sql
-- Phase 1.B: Nexus provider OAuth tokens for the v2 schema.
-- Replaces legacy public.nexus_provider_accounts. Token encryption key
-- (NEXUS_TOKEN_ENCRYPTION_KEY env var) is unchanged from v1.

CREATE TABLE IF NOT EXISTS pipelines.nexus_provider_tokens (
    profile_id      uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    provider        text NOT NULL,
    encrypted_token bytea NOT NULL,
    refresh_token   bytea,
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, provider)
);

-- Round-2 R2.2: covering index for the FK + RLS predicate. The PK is
-- (profile_id, provider) so workspace_id is otherwise unindexed.
CREATE INDEX IF NOT EXISTS idx_nexus_tokens_workspace_id
    ON pipelines.nexus_provider_tokens (workspace_id);

ALTER TABLE pipelines.nexus_provider_tokens ENABLE ROW LEVEL SECURITY;

-- PostgREST/role grants. RLS still enforces row visibility; without these
-- table-level grants the authenticated role gets a 42501 'permission denied'
-- before RLS is even consulted. Service-role connections bypass RLS via the
-- nexus_tokens_service_all policy below.
GRANT SELECT, INSERT, UPDATE, DELETE ON pipelines.nexus_provider_tokens
    TO authenticated, service_role;

-- R2.1 deviation: the plan prescribed `ANY ((SELECT core.jwt_workspace_ids()))`
-- to cache the STABLE-function call per query, but jwt_workspace_ids() returns
-- uuid[] — the scalar-subquery wrap turns the comparison into `uuid = uuid[]`
-- (operator does not exist). Match the canonical pattern from
-- _v2/08_rls_policies.sql which calls the function unwrapped; the planner
-- already memoises STABLE function calls with no row-dependent args. The
-- service-role check is scalar (text) so the wrap is kept there.
CREATE POLICY nexus_tokens_select ON pipelines.nexus_provider_tokens
  FOR SELECT USING (workspace_id = ANY (core.jwt_workspace_ids()));

CREATE POLICY nexus_tokens_insert ON pipelines.nexus_provider_tokens
  FOR INSERT WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));

CREATE POLICY nexus_tokens_update ON pipelines.nexus_provider_tokens
  FOR UPDATE USING (workspace_id = ANY (core.jwt_workspace_ids()));

CREATE POLICY nexus_tokens_delete ON pipelines.nexus_provider_tokens
  FOR DELETE USING (workspace_id = ANY (core.jwt_workspace_ids()));

CREATE POLICY nexus_tokens_service_all ON pipelines.nexus_provider_tokens
  FOR ALL
  USING ((SELECT current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role')
  WITH CHECK ((SELECT current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role');

-- updated_at maintenance trigger. No existing fn_set_updated_at exists
-- elsewhere in the v2 schema (verified via grep across _v2/*.sql), so the
-- function is defined here in pipelines and attached only to this table.
CREATE OR REPLACE FUNCTION pipelines.fn_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_nexus_tokens_set_updated_at ON pipelines.nexus_provider_tokens;
CREATE TRIGGER trg_nexus_tokens_set_updated_at
    BEFORE UPDATE ON pipelines.nexus_provider_tokens
    FOR EACH ROW EXECUTE FUNCTION pipelines.fn_set_updated_at();

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
