-- Phase 8.0.6: drop the 6 retained public.pricing_* tables + 6 RPCs.
-- All 0 rows; verified via Phase 7.4 audit + Phase 8.0 inspection.
-- v2 canonical = billing.* (already shipped); pricing module migrated in Tasks 1+2.
-- Pre-DROP audit (2026-05-11): zero live website/ code refs to public.pricing_*.

-- Drop RPCs (CASCADE clears PG-internal grants/comments only).
DROP FUNCTION IF EXISTS public.pricing_active_plan(text) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_check_entitlement(text, text, text) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_plan_cap(text, text, text) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_add_pack_credits(text, text, integer) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_deduct_pack_credits(text, text, integer) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_touch_updated_at() CASCADE;

-- Drop tables (RESTRICT default; no row-data to lose).
DROP TABLE IF EXISTS public.pricing_balances RESTRICT;
DROP TABLE IF EXISTS public.pricing_disputes RESTRICT;
DROP TABLE IF EXISTS public.pricing_payment_events RESTRICT;
DROP TABLE IF EXISTS public.pricing_plan_cache RESTRICT;
DROP TABLE IF EXISTS public.pricing_refunds RESTRICT;
DROP TABLE IF EXISTS public.pricing_webhook_events RESTRICT;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
