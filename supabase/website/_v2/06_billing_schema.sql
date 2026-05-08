-- DB v2 billing schema: Razorpay/billing tables rekeyed to core.profiles.

CREATE SCHEMA IF NOT EXISTS billing;

CREATE TABLE IF NOT EXISTS billing.pricing_billing_profiles (
    profile_id                  uuid PRIMARY KEY REFERENCES core.profiles(id) ON DELETE CASCADE,
    email                       text,
    name                        text,
    razorpay_customer_id        text UNIQUE,
    razorpay_subscriber_id      text UNIQUE,
    default_currency            text NOT NULL DEFAULT 'INR',
    metadata                    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing.pricing_orders (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id                    uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    kind                          text,
    amount                        bigint,
    amount_paise                  bigint,
    currency                      text NOT NULL DEFAULT 'INR',
    plan_id                       text,
    period_id                     text,
    status                        text NOT NULL DEFAULT 'created',
    razorpay_order_id             text UNIQUE,
    razorpay_subscription_id      text,
    razorpay_payment_id           text,
    provider_order_id             text,
    provider_subscription_id      text,
    provider_payload              jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_reason                text,
    paid_at                       timestamptz,
    created_at                    timestamptz NOT NULL DEFAULT now(),
    updated_at                    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pricing_orders_profile_idx ON billing.pricing_orders(profile_id);
CREATE INDEX IF NOT EXISTS pricing_orders_kind_idx ON billing.pricing_orders(kind);
CREATE INDEX IF NOT EXISTS pricing_orders_rzp_order_idx ON billing.pricing_orders(razorpay_order_id);
CREATE INDEX IF NOT EXISTS pricing_orders_rzp_payment_idx ON billing.pricing_orders(razorpay_payment_id);
CREATE INDEX IF NOT EXISTS pricing_orders_rzp_sub_idx ON billing.pricing_orders(razorpay_subscription_id);

CREATE TABLE IF NOT EXISTS billing.pricing_subscriptions (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id                    uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    plan_id                       text NOT NULL,
    period_id                     text,
    status                        text NOT NULL,
    total_count                   integer,
    paid_count                    integer NOT NULL DEFAULT 0,
    current_period_start          timestamptz,
    current_period_end            timestamptz,
    cancelled_at                  timestamptz,
    failure_reason                text,
    razorpay_subscription_id      text UNIQUE,
    razorpay_payment_id           text,
    provider_subscription_id      text,
    provider_payload              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at                    timestamptz NOT NULL DEFAULT now(),
    updated_at                    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pricing_subscriptions_profile_idx ON billing.pricing_subscriptions(profile_id);
CREATE INDEX IF NOT EXISTS pricing_subscriptions_rzp_idx ON billing.pricing_subscriptions(razorpay_subscription_id);
CREATE INDEX IF NOT EXISTS pricing_subscriptions_status_idx
    ON billing.pricing_subscriptions(status, current_period_end);

CREATE TABLE IF NOT EXISTS billing.pricing_balances (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id  uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    meter       text NOT NULL,
    balance     bigint NOT NULL DEFAULT 0 CHECK (balance >= 0),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_id, meter)
);

CREATE INDEX IF NOT EXISTS pricing_balances_profile_idx ON billing.pricing_balances(profile_id);

CREATE TABLE IF NOT EXISTS billing.pricing_payment_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id    text NOT NULL UNIQUE,
    event_type  text NOT NULL,
    payment_id  text,
    profile_id  uuid REFERENCES core.profiles(id) ON DELETE SET NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pricing_payment_events_payment_idx ON billing.pricing_payment_events(payment_id);
CREATE INDEX IF NOT EXISTS pricing_payment_events_type_idx ON billing.pricing_payment_events(event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS billing.pricing_plan_cache (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cache_key          text NOT NULL UNIQUE,
    period_id          text NOT NULL,
    amount             bigint NOT NULL,
    razorpay_plan_id   text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pricing_plan_cache_period_idx ON billing.pricing_plan_cache(period_id, amount);

CREATE TABLE IF NOT EXISTS billing.pricing_refunds (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    razorpay_refund_id    text NOT NULL UNIQUE,
    razorpay_payment_id   text,
    payment_id            text,
    profile_id            uuid REFERENCES core.profiles(id) ON DELETE SET NULL,
    amount                bigint NOT NULL,
    currency              text NOT NULL DEFAULT 'INR',
    status                text NOT NULL CHECK (status IN ('created', 'processed', 'failed')),
    speed                 text,
    notes                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pricing_refunds_payment_idx ON billing.pricing_refunds(payment_id);
CREATE INDEX IF NOT EXISTS pricing_refunds_profile_idx ON billing.pricing_refunds(profile_id);

CREATE TABLE IF NOT EXISTS billing.pricing_disputes (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    razorpay_dispute_id    text NOT NULL UNIQUE,
    razorpay_payment_id    text,
    payment_id             text,
    profile_id             uuid REFERENCES core.profiles(id) ON DELETE SET NULL,
    amount                 bigint NOT NULL,
    currency               text NOT NULL DEFAULT 'INR',
    phase                  text NOT NULL CHECK (phase IN ('created', 'under_review', 'action_required', 'won', 'lost', 'closed')),
    reason_code            text,
    payload                jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pricing_disputes_payment_idx ON billing.pricing_disputes(payment_id);
CREATE INDEX IF NOT EXISTS pricing_disputes_profile_idx ON billing.pricing_disputes(profile_id);

CREATE TABLE IF NOT EXISTS billing.pricing_plan_entitlements (
    plan_id        text NOT NULL,
    feature        text NOT NULL,
    unit           text NOT NULL,
    monthly_limit  numeric NOT NULL,
    is_hard_cap    boolean NOT NULL DEFAULT true,
    PRIMARY KEY (plan_id, feature, unit)
);

CREATE TABLE IF NOT EXISTS billing.pricing_entitlement_consumption (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id    uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    workspace_id  uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    feature       text NOT NULL,
    unit          text NOT NULL,
    quantity      numeric NOT NULL,
    consumed_at   timestamptz NOT NULL DEFAULT now(),
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS billing.pricing_webhook_events (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider       text NOT NULL DEFAULT 'razorpay',
    event_id       text NOT NULL,
    event_type     text NOT NULL,
    processed_at   timestamptz,
    payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, event_id)
);

CREATE OR REPLACE FUNCTION billing.pricing_add_pack_credits(
    p_profile_id uuid,
    p_meter text,
    p_quantity integer
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    new_balance bigint;
BEGIN
    INSERT INTO billing.pricing_balances (profile_id, meter, balance, updated_at)
    VALUES (p_profile_id, p_meter, p_quantity, now())
    ON CONFLICT (profile_id, meter)
    DO UPDATE SET
        balance = billing.pricing_balances.balance + EXCLUDED.balance,
        updated_at = now()
    RETURNING balance INTO new_balance;

    RETURN new_balance;
END
$$;

CREATE OR REPLACE FUNCTION billing.pricing_deduct_pack_credits(
    p_profile_id uuid,
    p_meter text,
    p_quantity integer
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    new_balance bigint;
BEGIN
    INSERT INTO billing.pricing_balances (profile_id, meter, balance, updated_at)
    VALUES (p_profile_id, p_meter, 0, now())
    ON CONFLICT (profile_id, meter) DO NOTHING;

    UPDATE billing.pricing_balances
       SET balance = greatest(0, balance - p_quantity),
           updated_at = now()
     WHERE profile_id = p_profile_id
       AND meter = p_meter
     RETURNING balance INTO new_balance;

    RETURN COALESCE(new_balance, 0);
END
$$;

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
