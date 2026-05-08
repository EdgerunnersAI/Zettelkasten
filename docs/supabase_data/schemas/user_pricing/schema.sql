-- ============================================================================
-- user_pricing — Razorpay billing schema (ADDITIVE)
-- ============================================================================
-- The legacy migration `supabase/website/kg_public/migrations/2026-05-01_user_pricing.sql`
-- already created `pricing_billing_profiles`, `pricing_orders`, and
-- `pricing_subscriptions` with a different column layout (amount_paise,
-- provider_order_id, provider_subscription_id, provider_payload, etc.).
--
-- This file is **additive only** — it ALTERs those legacy tables to add the
-- columns the new Razorpay routes need, and CREATEs the genuinely new tables
-- (balances, plan_cache, payment_events, refunds, disputes). It is safe to
-- run repeatedly.
--
-- Apply via the GH Actions workflow `apply-pricing-schema.yml` (preferred —
-- the SUPABASE_DB_URL secret stays inside the runner) or manually:
--   psql "$SUPABASE_DB_URL" -f supabase/website/user_pricing/schema.sql
-- ============================================================================

create extension if not exists "pgcrypto";

-- ── pricing_orders — additive columns for the Razorpay flow ────────────────
alter table public.pricing_orders
    add column if not exists kind                     text,
    add column if not exists amount                   bigint,
    add column if not exists plan_id                  text,
    add column if not exists period_id                text,
    add column if not exists razorpay_order_id        text,
    add column if not exists razorpay_subscription_id text,
    add column if not exists razorpay_payment_id      text,
    add column if not exists failure_reason           text,
    add column if not exists paid_at                  timestamptz;

-- Backfill `amount` from legacy `amount_paise` for any historical rows so
-- queries on the new column never see a NULL where data existed.
update public.pricing_orders
   set amount = amount_paise
 where amount is null
   and amount_paise is not null;

create index if not exists pricing_orders_kind_idx
    on public.pricing_orders (kind);
create index if not exists pricing_orders_rzp_order_idx
    on public.pricing_orders (razorpay_order_id);
create index if not exists pricing_orders_rzp_payment_idx
    on public.pricing_orders (razorpay_payment_id);
create index if not exists pricing_orders_rzp_sub_idx
    on public.pricing_orders (razorpay_subscription_id);

-- ── pricing_subscriptions — additive columns for full lifecycle ────────────
alter table public.pricing_subscriptions
    add column if not exists period_id                text,
    add column if not exists total_count              integer,
    add column if not exists paid_count               integer not null default 0,
    add column if not exists cancelled_at             timestamptz,
    add column if not exists failure_reason           text,
    add column if not exists razorpay_subscription_id text,
    add column if not exists razorpay_payment_id      text;

update public.pricing_subscriptions
   set razorpay_subscription_id = provider_subscription_id
 where razorpay_subscription_id is null
   and provider_subscription_id is not null;

create index if not exists pricing_subscriptions_rzp_idx
    on public.pricing_subscriptions (razorpay_subscription_id);
create index if not exists pricing_subscriptions_status_idx
    on public.pricing_subscriptions (status, current_period_end);

-- ── pricing_balances (NEW — pack credit wallet per user/meter) ─────────────
create table if not exists public.pricing_balances (
    id              uuid primary key default gen_random_uuid(),
    render_user_id  text not null,
    meter           text not null,
    balance         bigint not null default 0 check (balance >= 0),
    updated_at      timestamptz not null default now(),
    unique (render_user_id, meter)
);

create index if not exists pricing_balances_user_idx
    on public.pricing_balances (render_user_id);

-- ── pricing_payment_events (NEW — webhook event log w/ idempotency) ────────
create table if not exists public.pricing_payment_events (
    id           uuid primary key default gen_random_uuid(),
    event_id     text not null unique,
    event_type   text not null,
    payment_id   text,
    payload      jsonb not null default '{}'::jsonb,
    created_at   timestamptz not null default now()
);

create index if not exists pricing_payment_events_payment_idx
    on public.pricing_payment_events (payment_id);
create index if not exists pricing_payment_events_type_idx
    on public.pricing_payment_events (event_type, created_at desc);

-- ── pricing_plan_cache (NEW — period_id+amount → razorpay_plan_id) ─────────
-- Razorpay Plans are immutable on amount + interval. We cache by period_id
-- + amount so launch/list price flips mint a new plan rather than reusing
-- one with the wrong amount.
create table if not exists public.pricing_plan_cache (
    id                  uuid primary key default gen_random_uuid(),
    cache_key           text not null unique,   -- "{period_id}:{amount}"
    period_id           text not null,
    amount              bigint not null,
    razorpay_plan_id    text not null,
    created_at          timestamptz not null default now()
);

create index if not exists pricing_plan_cache_period_idx
    on public.pricing_plan_cache (period_id, amount);

-- ── pricing_refunds (NEW — audit trail for refund.* events) ────────────────
create table if not exists public.pricing_refunds (
    id                  uuid primary key default gen_random_uuid(),
    razorpay_refund_id  text not null unique,
    razorpay_payment_id text,
    payment_id          text,
    render_user_id      text,
    amount              bigint not null,
    currency            text not null default 'INR',
    status              text not null check (status in ('created', 'processed', 'failed')),
    speed               text,
    notes               jsonb not null default '{}'::jsonb,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists pricing_refunds_payment_idx
    on public.pricing_refunds (payment_id);
create index if not exists pricing_refunds_user_idx
    on public.pricing_refunds (render_user_id);

-- ── pricing_disputes (NEW — audit trail for payment.dispute.* events) ──────
create table if not exists public.pricing_disputes (
    id                  uuid primary key default gen_random_uuid(),
    razorpay_dispute_id text not null unique,
    razorpay_payment_id text,
    payment_id          text,
    render_user_id      text,
    amount              bigint not null,
    currency            text not null default 'INR',
    phase               text not null
                            check (phase in ('created', 'under_review', 'action_required', 'won', 'lost', 'closed')),
    reason_code         text,
    payload             jsonb not null default '{}'::jsonb,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists pricing_disputes_payment_idx
    on public.pricing_disputes (payment_id);
create index if not exists pricing_disputes_user_idx
    on public.pricing_disputes (render_user_id);

-- ── credit-add RPC (atomic upsert for pack fulfillment) ────────────────────
create or replace function public.pricing_add_pack_credits(
    p_render_user_id text,
    p_meter          text,
    p_quantity       integer
) returns bigint
language plpgsql
security definer
as $$
declare
    new_balance bigint;
begin
    insert into public.pricing_balances (render_user_id, meter, balance, updated_at)
    values (p_render_user_id, p_meter, p_quantity, now())
    on conflict (render_user_id, meter)
    do update set
        balance    = pricing_balances.balance + excluded.balance,
        updated_at = now()
    returning balance into new_balance;
    return new_balance;
end;
$$;

-- ── credit-deduct RPC (clamp-at-zero for refund / lost dispute) ────────────
create or replace function public.pricing_deduct_pack_credits(
    p_render_user_id text,
    p_meter          text,
    p_quantity       integer
) returns bigint
language plpgsql
security definer
as $$
declare
    new_balance bigint;
begin
    insert into public.pricing_balances (render_user_id, meter, balance, updated_at)
    values (p_render_user_id, p_meter, 0, now())
    on conflict (render_user_id, meter) do nothing;

    update public.pricing_balances
        set balance    = greatest(0, balance - p_quantity),
            updated_at = now()
        where render_user_id = p_render_user_id and meter = p_meter
        returning balance into new_balance;
    return coalesce(new_balance, 0);
end;
$$;

-- ── updated_at trigger helper (idempotent — uses CREATE OR REPLACE) ────────
create or replace function public.pricing_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists pricing_orders_touch on public.pricing_orders;
create trigger pricing_orders_touch
    before update on public.pricing_orders
    for each row execute function public.pricing_touch_updated_at();

drop trigger if exists pricing_subscriptions_touch on public.pricing_subscriptions;
create trigger pricing_subscriptions_touch
    before update on public.pricing_subscriptions
    for each row execute function public.pricing_touch_updated_at();

drop trigger if exists pricing_billing_profiles_touch on public.pricing_billing_profiles;
create trigger pricing_billing_profiles_touch
    before update on public.pricing_billing_profiles
    for each row execute function public.pricing_touch_updated_at();

drop trigger if exists pricing_balances_touch on public.pricing_balances;
create trigger pricing_balances_touch
    before update on public.pricing_balances
    for each row execute function public.pricing_touch_updated_at();

drop trigger if exists pricing_refunds_touch on public.pricing_refunds;
create trigger pricing_refunds_touch
    before update on public.pricing_refunds
    for each row execute function public.pricing_touch_updated_at();

drop trigger if exists pricing_disputes_touch on public.pricing_disputes;
create trigger pricing_disputes_touch
    before update on public.pricing_disputes
    for each row execute function public.pricing_touch_updated_at();

-- ── RLS — service-role-only (anon clients hit RPCs / endpoints, never SQL) ─
alter table public.pricing_balances          enable row level security;
alter table public.pricing_payment_events    enable row level security;
alter table public.pricing_plan_cache        enable row level security;
alter table public.pricing_refunds           enable row level security;
alter table public.pricing_disputes          enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'pricing_balances' and policyname = 'service_role_all'
    ) then
        create policy service_role_all on public.pricing_balances
            for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'pricing_payment_events' and policyname = 'service_role_all'
    ) then
        create policy service_role_all on public.pricing_payment_events
            for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'pricing_plan_cache' and policyname = 'service_role_all'
    ) then
        create policy service_role_all on public.pricing_plan_cache
            for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'pricing_refunds' and policyname = 'service_role_all'
    ) then
        create policy service_role_all on public.pricing_refunds
            for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'pricing_disputes' and policyname = 'service_role_all'
    ) then
        create policy service_role_all on public.pricing_disputes
            for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
    end if;
end $$;

-- end of schema
