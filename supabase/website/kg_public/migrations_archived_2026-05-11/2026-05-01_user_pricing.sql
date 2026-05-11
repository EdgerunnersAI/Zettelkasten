-- User pricing, payments, and entitlement accounting.

CREATE TABLE IF NOT EXISTS pricing_billing_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES kg_users(id) ON DELETE CASCADE,
    render_user_id  TEXT UNIQUE NOT NULL,
    email           TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pricing_orders (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID REFERENCES kg_users(id) ON DELETE CASCADE,
    render_user_id          TEXT NOT NULL,
    payment_id              TEXT UNIQUE NOT NULL,
    provider_order_id       TEXT UNIQUE,
    product_id              TEXT NOT NULL,
    meter                   TEXT,
    quantity                INTEGER NOT NULL DEFAULT 0,
    amount_paise            INTEGER NOT NULL,
    currency                TEXT NOT NULL DEFAULT 'INR',
    status                  TEXT NOT NULL DEFAULT 'created',
    idempotency_key         TEXT UNIQUE,
    provider_payload        JSONB NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pricing_subscriptions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID REFERENCES kg_users(id) ON DELETE CASCADE,
    render_user_id              TEXT NOT NULL,
    payment_id                  TEXT UNIQUE NOT NULL,
    provider_subscription_id    TEXT UNIQUE,
    plan_id                     TEXT NOT NULL,
    billing_period              TEXT NOT NULL,
    amount_paise                INTEGER NOT NULL,
    currency                    TEXT NOT NULL DEFAULT 'INR',
    status                      TEXT NOT NULL DEFAULT 'created',
    current_period_start        TIMESTAMPTZ,
    current_period_end          TIMESTAMPTZ,
    cancel_at_period_end        BOOLEAN NOT NULL DEFAULT false,
    provider_payload            JSONB NOT NULL DEFAULT '{}',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pricing_webhook_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider            TEXT NOT NULL DEFAULT '',
    event_id            TEXT,
    event_type          TEXT NOT NULL,
    object_id           TEXT,
    signature_hash      TEXT NOT NULL,
    payload             JSONB NOT NULL,
    processed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(provider, signature_hash),
    UNIQUE(provider, event_id)
);

CREATE TABLE IF NOT EXISTS pricing_credit_ledger (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES kg_users(id) ON DELETE CASCADE,
    render_user_id  TEXT NOT NULL,
    meter           TEXT NOT NULL CHECK (meter IN ('zettel', 'kasten', 'rag_question')),
    delta           INTEGER NOT NULL,
    source          TEXT NOT NULL,
    source_id       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pricing_usage_counters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES kg_users(id) ON DELETE CASCADE,
    render_user_id  TEXT NOT NULL,
    meter           TEXT NOT NULL CHECK (meter IN ('zettel', 'kasten', 'rag_question')),
    period_type     TEXT NOT NULL CHECK (period_type IN ('day', 'week', 'month', 'total')),
    period_start    DATE NOT NULL,
    used_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(render_user_id, meter, period_type, period_start)
);

CREATE INDEX IF NOT EXISTS idx_pricing_usage_lookup
ON pricing_usage_counters(render_user_id, meter, period_type, period_start);

CREATE INDEX IF NOT EXISTS idx_pricing_credit_lookup
ON pricing_credit_ledger(render_user_id, meter, created_at);

CREATE OR REPLACE FUNCTION pricing_active_plan(p_render_user_id TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(
        (
            SELECT plan_id
            FROM pricing_subscriptions
            WHERE render_user_id = p_render_user_id
              AND status IN ('active', 'authorized', 'paid')
            ORDER BY current_period_end DESC NULLS LAST, created_at DESC
            LIMIT 1
        ),
        'free'
    );
$$;

CREATE OR REPLACE FUNCTION pricing_plan_cap(p_plan_id TEXT, p_meter TEXT, p_period_type TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
    IF p_plan_id = 'max' THEN
        IF p_meter = 'zettel' AND p_period_type = 'day' THEN RETURN 30; END IF;
        IF p_meter = 'zettel' AND p_period_type = 'week' THEN RETURN 100; END IF;
        IF p_meter = 'zettel' AND p_period_type = 'month' THEN RETURN 200; END IF;
        IF p_meter = 'kasten' AND p_period_type = 'week' THEN RETURN 5; END IF;
        IF p_meter = 'kasten' AND p_period_type = 'total' THEN RETURN 50; END IF;
        IF p_meter = 'rag_question' AND p_period_type = 'month' THEN RETURN 500; END IF;
    ELSIF p_plan_id = 'basic' THEN
        IF p_meter = 'zettel' AND p_period_type = 'day' THEN RETURN 5; END IF;
        IF p_meter = 'zettel' AND p_period_type = 'week' THEN RETURN 30; END IF;
        IF p_meter = 'zettel' AND p_period_type = 'month' THEN RETURN 50; END IF;
        IF p_meter = 'kasten' AND p_period_type = 'total' THEN RETURN 5; END IF;
        IF p_meter = 'rag_question' AND p_period_type = 'month' THEN RETURN 100; END IF;
    ELSE
        IF p_meter = 'zettel' AND p_period_type = 'day' THEN RETURN 2; END IF;
        IF p_meter = 'zettel' AND p_period_type = 'week' THEN RETURN 10; END IF;
        IF p_meter = 'zettel' AND p_period_type = 'month' THEN RETURN 30; END IF;
        IF p_meter = 'kasten' AND p_period_type = 'total' THEN RETURN 1; END IF;
        IF p_meter = 'rag_question' AND p_period_type = 'month' THEN RETURN 30; END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION pricing_check_entitlement(
    p_render_user_id TEXT,
    p_meter TEXT,
    p_action_id TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_plan TEXT;
    v_day_used INTEGER;
    v_week_used INTEGER;
    v_month_used INTEGER;
    v_total_used INTEGER;
    v_credits INTEGER;
BEGIN
    -- Lock the user's current usage rows so concurrent checks do not race
    -- with immediate consume calls in the same request burst.
    PERFORM 1
    FROM pricing_usage_counters
    WHERE render_user_id = p_render_user_id
      AND meter = p_meter
    FOR UPDATE;

    v_plan := pricing_active_plan(p_render_user_id);

    SELECT COALESCE(SUM(delta), 0)
    INTO v_credits
    FROM pricing_credit_ledger
    WHERE render_user_id = p_render_user_id
      AND meter = p_meter;

    SELECT COALESCE(MAX(used_count), 0) INTO v_day_used
    FROM pricing_usage_counters
    WHERE render_user_id = p_render_user_id AND meter = p_meter AND period_type = 'day' AND period_start = current_date;

    SELECT COALESCE(MAX(used_count), 0) INTO v_week_used
    FROM pricing_usage_counters
    WHERE render_user_id = p_render_user_id AND meter = p_meter AND period_type = 'week' AND period_start = date_trunc('week', current_date)::date;

    SELECT COALESCE(MAX(used_count), 0) INTO v_month_used
    FROM pricing_usage_counters
    WHERE render_user_id = p_render_user_id AND meter = p_meter AND period_type = 'month' AND period_start = date_trunc('month', current_date)::date;

    SELECT COALESCE(MAX(used_count), 0) INTO v_total_used
    FROM pricing_usage_counters
    WHERE render_user_id = p_render_user_id AND meter = p_meter AND period_type = 'total' AND period_start = DATE '1970-01-01';

    IF pricing_plan_cap(v_plan, p_meter, 'day') IS NOT NULL AND v_day_used >= pricing_plan_cap(v_plan, p_meter, 'day') THEN
        RETURN v_credits > 0;
    END IF;
    IF pricing_plan_cap(v_plan, p_meter, 'week') IS NOT NULL AND v_week_used >= pricing_plan_cap(v_plan, p_meter, 'week') THEN
        RETURN v_credits > 0;
    END IF;
    IF pricing_plan_cap(v_plan, p_meter, 'month') IS NOT NULL AND v_month_used >= pricing_plan_cap(v_plan, p_meter, 'month') THEN
        RETURN v_credits > 0;
    END IF;
    IF pricing_plan_cap(v_plan, p_meter, 'total') IS NOT NULL AND v_total_used >= pricing_plan_cap(v_plan, p_meter, 'total') THEN
        RETURN v_credits > 0;
    END IF;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION pricing_consume_entitlement(
    p_render_user_id TEXT,
    p_meter TEXT,
    p_action_id TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_allowed BOOLEAN;
BEGIN
    v_allowed := pricing_check_entitlement(p_render_user_id, p_meter, p_action_id);
    IF NOT v_allowed THEN
        RETURN FALSE;
    END IF;

    INSERT INTO pricing_usage_counters(render_user_id, meter, period_type, period_start, used_count)
    VALUES
        (p_render_user_id, p_meter, 'day', current_date, 1),
        (p_render_user_id, p_meter, 'week', date_trunc('week', current_date)::date, 1),
        (p_render_user_id, p_meter, 'month', date_trunc('month', current_date)::date, 1),
        (p_render_user_id, p_meter, 'total', DATE '1970-01-01', 1)
    ON CONFLICT(render_user_id, meter, period_type, period_start)
    DO UPDATE SET used_count = pricing_usage_counters.used_count + 1, updated_at = now();

    RETURN TRUE;
END;
$$;
