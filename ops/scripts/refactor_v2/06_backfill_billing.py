"""Backfill billing.pricing_* tables from legacy public pricing tables."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import run_statements, load_config, parse_args, require_continue, run_async  # noqa: E402


SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM (
              SELECT render_user_id FROM public.pricing_billing_profiles
              UNION SELECT render_user_id FROM public.pricing_orders
              UNION SELECT render_user_id FROM public.pricing_subscriptions
              UNION SELECT render_user_id FROM public.pricing_balances
              UNION SELECT render_user_id FROM public.pricing_refunds
              UNION SELECT render_user_id FROM public.pricing_disputes
          ) refs
          LEFT JOIN core.profiles p ON p.id::text = refs.render_user_id
         WHERE refs.render_user_id IS NOT NULL
           AND p.id IS NULL
    ) THEN
        RAISE EXCEPTION 'billing backfill has unresolvable render_user_id/profile mappings';
    END IF;
END $$;

INSERT INTO billing.pricing_plan_entitlements (plan_id, feature, unit, monthly_limit, is_hard_cap)
VALUES
    ('free', 'zettel', 'count', 30, true),
    ('free', 'kasten', 'count', 1, true),
    ('free', 'rag_question', 'count', 30, true),
    ('basic', 'zettel', 'count', 50, true),
    ('basic', 'kasten', 'count', 5, true),
    ('basic', 'rag_question', 'count', 100, true),
    ('max', 'zettel', 'count', 200, true),
    ('max', 'kasten', 'count', 50, true),
    ('max', 'rag_question', 'count', 500, true)
ON CONFLICT DO NOTHING;

INSERT INTO billing.pricing_billing_profiles (
    profile_id, email, name, razorpay_customer_id, razorpay_subscriber_id, metadata, created_at, updated_at
)
SELECT p.id,
       bp.email,
       bp.name,
       NULL,
       NULL,
       jsonb_build_object('legacy_billing_profile_id', bp.id, 'phone', bp.phone),
       bp.created_at,
       bp.updated_at
  FROM public.pricing_billing_profiles bp
  JOIN core.profiles p ON p.id::text = bp.render_user_id
ON CONFLICT (profile_id) DO UPDATE
SET email = EXCLUDED.email,
    name = EXCLUDED.name,
    razorpay_subscriber_id = EXCLUDED.razorpay_subscriber_id,
    metadata = EXCLUDED.metadata,
    updated_at = now();

INSERT INTO billing.pricing_orders (
    id, profile_id, kind, amount, amount_paise, currency, plan_id, period_id,
    status, razorpay_order_id, razorpay_subscription_id, razorpay_payment_id,
    provider_order_id, provider_subscription_id, provider_payload, failure_reason,
    paid_at, created_at, updated_at
)
SELECT o.id,
       p.id,
       o.kind,
       COALESCE(o.amount, o.amount_paise),
       o.amount_paise,
       o.currency,
       COALESCE(o.plan_id, o.product_id),
       o.period_id,
       o.status,
       o.razorpay_order_id,
       o.razorpay_subscription_id,
       o.razorpay_payment_id,
       o.provider_order_id,
       NULL,
       o.provider_payload,
       o.failure_reason,
       o.paid_at,
       o.created_at,
       o.updated_at
  FROM public.pricing_orders o
  JOIN core.profiles p ON p.id::text = o.render_user_id
ON CONFLICT (id) DO UPDATE
SET status = EXCLUDED.status,
    razorpay_payment_id = EXCLUDED.razorpay_payment_id,
    provider_payload = EXCLUDED.provider_payload,
    updated_at = now();

INSERT INTO billing.pricing_subscriptions (
    id, profile_id, plan_id, period_id, status, total_count, paid_count,
    current_period_start, current_period_end, cancelled_at, failure_reason,
    razorpay_subscription_id, razorpay_payment_id, provider_subscription_id,
    provider_payload, created_at, updated_at
)
SELECT s.id,
       p.id,
       s.plan_id,
       COALESCE(s.period_id, s.billing_period),
       s.status,
       s.total_count,
       s.paid_count,
       s.current_period_start,
       s.current_period_end,
       s.cancelled_at,
       s.failure_reason,
       s.razorpay_subscription_id,
       s.razorpay_payment_id,
       s.provider_subscription_id,
       s.provider_payload,
       s.created_at,
       s.updated_at
  FROM public.pricing_subscriptions s
  JOIN core.profiles p ON p.id::text = s.render_user_id
ON CONFLICT (id) DO UPDATE
SET status = EXCLUDED.status,
    current_period_end = EXCLUDED.current_period_end,
    updated_at = now();

INSERT INTO billing.pricing_balances (id, profile_id, meter, balance, updated_at)
SELECT b.id, p.id, b.meter, b.balance, b.updated_at
  FROM public.pricing_balances b
  JOIN core.profiles p ON p.id::text = b.render_user_id
ON CONFLICT (profile_id, meter) DO UPDATE
SET balance = EXCLUDED.balance,
    updated_at = now();

INSERT INTO billing.pricing_payment_events (id, event_id, event_type, payment_id, payload, created_at)
SELECT id, event_id, event_type, payment_id, payload, created_at
  FROM public.pricing_payment_events
ON CONFLICT (event_id) DO NOTHING;

INSERT INTO billing.pricing_plan_cache (id, cache_key, period_id, amount, razorpay_plan_id, created_at)
SELECT id, cache_key, period_id, amount, razorpay_plan_id, created_at
  FROM public.pricing_plan_cache
ON CONFLICT (cache_key) DO UPDATE
SET razorpay_plan_id = EXCLUDED.razorpay_plan_id;

INSERT INTO billing.pricing_refunds (
    id, razorpay_refund_id, razorpay_payment_id, payment_id, profile_id,
    amount, currency, status, speed, notes, created_at, updated_at
)
SELECT r.id, r.razorpay_refund_id, r.razorpay_payment_id, r.payment_id, p.id,
       r.amount, r.currency, r.status, r.speed, r.notes, r.created_at, r.updated_at
  FROM public.pricing_refunds r
  LEFT JOIN core.profiles p ON p.id::text = r.render_user_id
ON CONFLICT (razorpay_refund_id) DO NOTHING;

INSERT INTO billing.pricing_disputes (
    id, razorpay_dispute_id, razorpay_payment_id, payment_id, profile_id,
    amount, currency, phase, reason_code, payload, created_at, updated_at
)
SELECT d.id, d.razorpay_dispute_id, d.razorpay_payment_id, d.payment_id, p.id,
       d.amount, d.currency, d.phase, d.reason_code, d.payload, d.created_at, d.updated_at
  FROM public.pricing_disputes d
  LEFT JOIN core.profiles p ON p.id::text = d.render_user_id
ON CONFLICT (razorpay_dispute_id) DO NOTHING;

INSERT INTO billing.pricing_webhook_events (provider, event_id, event_type, processed_at, payload, created_at)
SELECT provider,
       COALESCE(event_id, provider || ':' || signature_hash),
       event_type,
       processed_at,
       payload,
       created_at
  FROM public.pricing_webhook_events
ON CONFLICT (provider, event_id) DO NOTHING;
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "billing backfill")
    if args.dry_run:
        print("billing backfill ready; unresolvable Razorpay/profile mappings fail fast")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
