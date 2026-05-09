-- _v2/12_revert_unauthorized_pricing.sql
--
-- Revert of unauthorized changes made in 11_post_install.sql:
--   * 11 seeded billing.pricing_plan_entitlements with arbitrary per-plan
--     monthly_limit numbers that did NOT match the documented pricing model
--     in docs/research/pricing1.md. The actual model is daily/weekly/monthly
--     multi-cap (Free 2/10/30 zettels, Basic 5/30/50, Max 30/100/200) which
--     the current schema cannot represent with a single monthly_limit column.
--   * 11 also redefined pricing_consume_entitlement to default to the 'free'
--     plan when no subscription was found — that was a product decision that
--     belonged to the operator, not the migration author.
--
-- This file restores the function body shipped in 06_billing_schema.sql and
-- removes the rows added by 11. PostgREST schema exposure and the legacy
-- pricing_consume_entitlement(text,text,text) drop in 11 are kept — they
-- are infra bugfixes unrelated to pricing.
--
-- Schema-design follow-up (separate work, NOT done here): the operator must
-- decide whether pricing_plan_entitlements should grow daily_limit /
-- weekly_limit / monthly_limit columns, or whether multi-cap enforcement
-- moves into a different surface entirely. Either way, the seed values
-- belong to a billing-config commit by the operator, not to the schema
-- migration.

-- ── 1. Remove the unauthorized seeds ─────────────────────────────────────────
-- 12 rows of (plan_id, feature, unit, monthly_limit) inserted by 11.
DELETE FROM billing.pricing_plan_entitlements
 WHERE plan_id IN ('free', 'basic', 'pro')
   AND unit    = 'request'
   AND feature IN ('zettel', 'rag_query', 'summarize', 'kg_extract');

-- ── 2. Restore the original pricing_consume_entitlement body (verbatim ─────
-- copy of supabase/website/_v2/06_billing_schema.sql lines 217-263). No
-- default-to-free, no other behavioral change.
CREATE OR REPLACE FUNCTION billing.pricing_consume_entitlement(
    p_profile_id uuid,
    p_feature text,
    p_unit text
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    workspace_id uuid;
    plan text;
    limit_value numeric;
    used_value numeric;
BEGIN
    SELECT wm.workspace_id
      INTO workspace_id
      FROM core.workspace_members wm
     WHERE wm.profile_id = p_profile_id
     ORDER BY wm.added_at
     LIMIT 1;

    SELECT ps.plan_id
      INTO plan
      FROM billing.pricing_subscriptions ps
     WHERE ps.profile_id = p_profile_id
       AND ps.status IN ('active', 'authenticated')
     ORDER BY ps.created_at DESC
     LIMIT 1;

    SELECT monthly_limit
      INTO limit_value
      FROM billing.pricing_plan_entitlements
     WHERE plan_id = plan
       AND feature = p_feature
       AND unit = p_unit;

    IF workspace_id IS NULL OR limit_value IS NULL THEN
        RETURN false;
    END IF;

    SELECT COALESCE(sum(quantity_total), 0)
      INTO used_value
      FROM core.usage_aggregates
     WHERE profile_id = p_profile_id
       AND feature = p_feature
       AND unit = p_unit
       AND period_start >= date_trunc('month', now());

    RETURN used_value < limit_value;
END
$$;
