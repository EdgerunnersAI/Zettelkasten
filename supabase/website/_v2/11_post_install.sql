-- _v2/11_post_install.sql — post-deploy bootstrap for the v2 schema.
--
-- Idempotent. Three concerns:
--   1. Expose v2 schemas to PostgREST (role-level db_schemas).
--   2. Drop legacy v1 pricing_consume_entitlement signature so PostgREST RPC
--      lookups disambiguate to the v2 (uuid-keyed) function.
--   3. Seed default plan entitlements + redefine the entitlement check so
--      profiles without an explicit subscription default to the 'free' plan.
--      Without this every new profile hits 402 quota_exhausted on first
--      write because no pricing_subscriptions row exists yet.
--
-- The free-tier limits are intentionally conservative; tune via UPDATE in a
-- follow-up commit without touching the function logic.

-- ── 1. PostgREST schema exposure ─────────────────────────────────────────────
ALTER ROLE authenticator SET pgrst.db_schemas =
    'public, graphql_public, core, content, kg, rag, pipelines, billing';
NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';

-- ── 2. Drop legacy v1 pricing_consume_entitlement(text, text, text) ──────────
DROP FUNCTION IF EXISTS public.pricing_consume_entitlement(text, text, text) CASCADE;
DROP FUNCTION IF EXISTS billing.pricing_consume_entitlement(text, text, text) CASCADE;

-- ── 3. Seed plan entitlements ────────────────────────────────────────────────
INSERT INTO billing.pricing_plan_entitlements (plan_id, feature, unit, monthly_limit, is_hard_cap) VALUES
    ('free',  'zettel',     'request', 25,   true),
    ('free',  'rag_query',  'request', 50,   true),
    ('free',  'summarize',  'request', 25,   true),
    ('free',  'kg_extract', 'request', 25,   true),
    ('basic', 'zettel',     'request', 200,  true),
    ('basic', 'rag_query',  'request', 500,  true),
    ('basic', 'summarize',  'request', 200,  true),
    ('basic', 'kg_extract', 'request', 200,  true),
    ('pro',   'zettel',     'request', 2000, true),
    ('pro',   'rag_query',  'request', 5000, true),
    ('pro',   'summarize',  'request', 2000, true),
    ('pro',   'kg_extract', 'request', 2000, true)
ON CONFLICT (plan_id, feature, unit) DO UPDATE
   SET monthly_limit = EXCLUDED.monthly_limit,
       is_hard_cap   = EXCLUDED.is_hard_cap;

-- ── 4. Redefine pricing_consume_entitlement to default-to-free ──────────────
-- The 06_billing_schema.sql function returns false when no subscription row
-- exists. This redefinition treats "no subscription" as "free plan" so new
-- users have a working quota out of the box. Subscription rows still
-- override the default (paid plans take effect immediately).
CREATE OR REPLACE FUNCTION billing.pricing_consume_entitlement(
    p_profile_id uuid,
    p_feature    text,
    p_unit       text
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace_id uuid;
    v_plan         text;
    v_limit        numeric;
    v_used         numeric;
BEGIN
    -- Resolve the profile's primary workspace (created by the
    -- core.create_personal_workspace trigger on profile insert).
    SELECT wm.workspace_id
      INTO v_workspace_id
      FROM core.workspace_members wm
     WHERE wm.profile_id = p_profile_id
     ORDER BY wm.added_at
     LIMIT 1;

    IF v_workspace_id IS NULL THEN
        -- Profile has no workspace yet (trigger should have created one;
        -- defensive return). Deny.
        RETURN false;
    END IF;

    -- Pick the most recent active subscription, defaulting to 'free' if none.
    SELECT COALESCE(
        (SELECT ps.plan_id
           FROM billing.pricing_subscriptions ps
          WHERE ps.profile_id = p_profile_id
            AND ps.status IN ('active', 'authenticated')
          ORDER BY ps.created_at DESC
          LIMIT 1),
        'free'
    ) INTO v_plan;

    -- Look up the plan's limit for this feature/unit.
    SELECT monthly_limit
      INTO v_limit
      FROM billing.pricing_plan_entitlements
     WHERE plan_id = v_plan
       AND feature = p_feature
       AND unit    = p_unit;

    IF v_limit IS NULL THEN
        -- Unknown feature/unit on this plan: deny rather than silently allow.
        RETURN false;
    END IF;

    -- Sum usage in the current billing month from core.usage_aggregates.
    SELECT COALESCE(SUM(quantity_total), 0)
      INTO v_used
      FROM core.usage_aggregates
     WHERE profile_id = p_profile_id
       AND feature    = p_feature
       AND unit       = p_unit
       AND period_start >= date_trunc('month', now());

    RETURN v_used < v_limit;
END
$$;

GRANT EXECUTE ON FUNCTION billing.pricing_consume_entitlement(uuid, text, text)
    TO authenticated, service_role;
