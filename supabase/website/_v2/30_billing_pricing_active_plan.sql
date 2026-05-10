-- Phase 8.0.1: port public.pricing_active_plan(text) to billing.pricing_active_plan(uuid).
-- Verbatim semantics: status filter + ORDER BY preserved exactly; only arg type
-- and source schema swapped. Body captured 2026-05-10 via pg_get_functiondef on
-- v1 prod (public.pricing_active_plan(text)) — same SQL/STABLE definition,
-- same COALESCE-to-'free' fail-open default. Fail-open returning 'free' is
-- intentional: pre-Phase-9 the rest of the system treats 'free' as the lowest
-- entitlement tier, so a missing subscription row degrades gracefully rather
-- than blocking the request. Phase 9 enforcement (see
-- docs/db-v2/phase-9-pricing-enforcement-plan.md) will introduce a hard-fail
-- variant for write paths; this RPC remains the read-side default lookup.
CREATE OR REPLACE FUNCTION billing.pricing_active_plan(p_profile_id uuid)
RETURNS text
LANGUAGE sql
STABLE
AS $function$
    SELECT COALESCE(
        (
            SELECT plan_id
            FROM billing.pricing_subscriptions
            WHERE profile_id = p_profile_id
              AND status IN ('active', 'authorized', 'paid')
            ORDER BY current_period_end DESC NULLS LAST, created_at DESC
            LIMIT 1
        ),
        'free'
    );
$function$;

GRANT EXECUTE ON FUNCTION billing.pricing_active_plan(uuid)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
