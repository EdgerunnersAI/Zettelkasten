-- _v2/15_drop_legacy_tables.sql — DESTRUCTIVE: drop legacy v1 public.* tables.
--
-- Plan: docs/superpowers/plans/2026-05-09-website-features-v2-purge.md
-- Round-2 amendment R2.5 (CASCADE blast-radius pre-flight) + Round-1 6.2
-- (14-day soak guard). 30-object allow-list:
--   22 originals + 3 derivative views (kg_graph_view, kg_user_stats,
--   rag_sandbox_stats) + 5 legacy public.pricing_* tables (billing_profiles,
--   credit_ledger, orders, subscriptions, usage_counters) — operator chat
--   2026-05-10. The 5 pricing drops are LEGACY only; the v2 canonical
--   pricing module lives in billing.* and is untouched by this migration.
--
-- This file is idempotent (DROP IF EXISTS) but the soak guard intentionally
-- BLOCKS execution until 14 days have elapsed since 11_post_install.sql was
-- recorded in core._migrations_applied. The guard MUST stay intact for any
-- future fresh install of the v2 stack. One-shot operator overrides for the
-- purge cutover are executed via a side-channel driver script that bypasses
-- the soak guard but reuses the rest of this SQL verbatim — the override is
-- documented per occurrence in docs/db-v2/cutover-runbook.md.
--
-- Drops are RESTRICT (default) — never CASCADE. The pre-flight is a
-- 3-branch UNION: (1) direct pg_class<->pg_class deps via pg_depend, (2)
-- view/mview rewrite-rule deps via pg_rewrite indirection, (3) FK deps via
-- pg_constraint. If any object outside the allow-list depends on an
-- allow-list table by any of those mechanisms, the migration halts.

-- Pre-flight: enumerate every dependent object outside the allow-list.
-- Halt the migration if any dependent will be dropped that operator did not approve.
DO $$
DECLARE
    allow_list text[] := ARRAY[
        'public.kg_users', 'public.kg_nodes', 'public.kg_links', 'public.kg_node_chunks',
        'public.kg_usage_edges', 'public.kg_usage_edges_agg', 'public.kg_kasten_node_freq',
        'public.kg_bandit_posteriors', 'public.kg_extraction_blocklist', 'public.kg_kasten_metrics',
        'public.rag_sandboxes', 'public.rag_sandbox_members',
        'public.chat_sessions', 'public.chat_messages',
        'public.summary_batch_runs', 'public.summary_batch_items',
        'public.nexus_provider_accounts', 'public.nexus_oauth_states',
        'public.nexus_ingest_runs', 'public.nexus_ingested_artifacts',
        'public.recompute_runs', 'public._migrations_applied',
        -- Derivative views authorised under the 22-table approval (cutover 2026-05-10).
        'public.kg_graph_view', 'public.kg_user_stats', 'public.rag_sandbox_stats',
        -- Legacy public.pricing_* (operator chat 2026-05-10). v2 canonical
        -- pricing module is billing.* and is NOT in this allow-list.
        'public.pricing_billing_profiles', 'public.pricing_credit_ledger',
        'public.pricing_orders', 'public.pricing_subscriptions',
        'public.pricing_usage_counters'
    ];
    rec record;
    unexpected_dependents text := '';
BEGIN
    FOR rec IN
        SELECT DISTINCT
            n2.nspname || '.' || c2.relname AS dependent_object,
            n1.nspname || '.' || c1.relname AS legacy_table,
            c2.relkind AS dependent_kind
        FROM pg_depend d
        JOIN pg_class c1 ON c1.oid = d.refobjid
        JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
        JOIN pg_class c2 ON c2.oid = d.objid
        JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
        WHERE n1.nspname || '.' || c1.relname = ANY (allow_list)
          AND c2.oid <> c1.oid
          AND (n2.nspname || '.' || c2.relname) <> ALL (allow_list)
          AND c2.relkind IN ('r', 'v', 'm', 'f', 'p')
        -- View / materialized-view dependents (via pg_rewrite indirection).
        -- pg_depend stores view->table refs as rewrite-rule entries; the direct
        -- pg_class join above misses these because d.objid is the rewrite rule,
        -- not the view itself. Resolve via pg_rewrite.ev_class.
        UNION
        SELECT DISTINCT
            n2.nspname || '.' || c2.relname AS dependent_object,
            n1.nspname || '.' || c1.relname AS legacy_table,
            c2.relkind AS dependent_kind
          FROM pg_depend d
          JOIN pg_rewrite r ON r.oid = d.objid AND d.classid = 'pg_rewrite'::regclass
          JOIN pg_class c2 ON c2.oid = r.ev_class  -- the view/mview itself
          JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
          JOIN pg_class c1 ON c1.oid = d.refobjid
          JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
         WHERE n1.nspname || '.' || c1.relname = ANY (allow_list)
           AND c2.oid <> c1.oid
           AND (n2.nspname || '.' || c2.relname) <> ALL (allow_list)
           AND c2.relkind IN ('v', 'm')  -- view, materialized view
        -- FK dependents via pg_constraint. pg_depend records FK edges
        -- via pg_constraint OIDs, not the target table's pg_class OID,
        -- so the direct join on d.refobjid -> pg_class above misses them.
        UNION
        SELECT DISTINCT
            n2.nspname || '.' || c2.relname AS dependent_object,
            n1.nspname || '.' || c1.relname AS legacy_table,
            c2.relkind AS dependent_kind
          FROM pg_constraint con
          JOIN pg_class c1 ON c1.oid = con.confrelid       -- table the FK references
          JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
          JOIN pg_class c2 ON c2.oid = con.conrelid        -- table holding the FK
          JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
         WHERE con.contype = 'f'
           AND n1.nspname || '.' || c1.relname = ANY (allow_list)
           AND c2.oid <> c1.oid
           AND (n2.nspname || '.' || c2.relname) <> ALL (allow_list)
           AND c2.relkind = 'r'
    LOOP
        unexpected_dependents := unexpected_dependents
            || format(E'  - %s (kind=%s) depends on %s\n',
                     rec.dependent_object, rec.dependent_kind, rec.legacy_table);
    END LOOP;
    IF length(unexpected_dependents) > 0 THEN
        RAISE EXCEPTION E'Legacy table drop blocked: dependents outside allow-list:\n%\nOperator must approve each before re-running.', unexpected_dependents;
    END IF;
END $$;

-- 14-day soak guard (Round-1 amendment 6.2). Operator overrides via the
-- per-occurrence driver script in docs/db-v2/cutover-runbook.md.
DO $$
DECLARE
  cutover_at timestamptz;
BEGIN
  SELECT applied_at INTO cutover_at FROM core._migrations_applied
   WHERE name = '11_post_install.sql';
  IF cutover_at IS NULL THEN
    RAISE EXCEPTION 'Cannot drop legacy tables: 11_post_install.sql not applied';
  END IF;
  IF (now() - cutover_at) < INTERVAL '14 days' THEN
    RAISE EXCEPTION 'Legacy table drop blocked: only % days since cutover (need 14)',
      EXTRACT(DAY FROM now() - cutover_at);
  END IF;
END $$;

-- Drops are RESTRICT (default), NOT CASCADE. Pre-flight already proved no
-- unexpected dependents exist outside the allow-list. Order below is a
-- topological sort over the intra-allow-list FK + view-rewrite edges
-- (verified live 2026-05-10 via pg_constraint + pg_rewrite enumeration):
--   - 3 views read from kg_users/kg_nodes/kg_links/rag_sandbox_members/rag_sandboxes
--   - kg_usage_edges_agg (mview) reads from kg_usage_edges
--   - 5 pricing_* FK -> kg_users (must drop before kg_users)
--   - chat_messages -> chat_sessions, kg_users
--   - chat_sessions -> kg_users, rag_sandboxes
--   - kg_links, kg_node_chunks -> kg_nodes, kg_users
--   - kg_nodes -> kg_users
--   - rag_sandbox_members -> kg_nodes, kg_users, rag_sandboxes
--   - rag_sandboxes -> kg_users
--   - summary_batch_items -> summary_batch_runs, kg_users
--   - summary_batch_runs -> kg_users
--   - nexus_ingested_artifacts -> nexus_ingest_runs, nexus_provider_accounts, kg_users
--   - nexus_ingest_runs -> nexus_provider_accounts, kg_users
--   - nexus_provider_accounts -> kg_users
--   - kg_usage_edges -> kg_users

-- Tier 0: views and materialized view (read-only consumers).
DROP VIEW IF EXISTS public.kg_graph_view RESTRICT;
DROP VIEW IF EXISTS public.kg_user_stats RESTRICT;
DROP VIEW IF EXISTS public.rag_sandbox_stats RESTRICT;
DROP MATERIALIZED VIEW IF EXISTS public.kg_usage_edges_agg RESTRICT;

-- Tier 1: tables that hold FKs but are not depended on by other allow-list tables.
DROP TABLE IF EXISTS public.pricing_credit_ledger RESTRICT;
DROP TABLE IF EXISTS public.pricing_usage_counters RESTRICT;
DROP TABLE IF EXISTS public.pricing_orders RESTRICT;
DROP TABLE IF EXISTS public.pricing_subscriptions RESTRICT;
DROP TABLE IF EXISTS public.pricing_billing_profiles RESTRICT;
DROP TABLE IF EXISTS public.kg_kasten_metrics RESTRICT;
DROP TABLE IF EXISTS public.kg_kasten_node_freq RESTRICT;
DROP TABLE IF EXISTS public.kg_bandit_posteriors RESTRICT;
DROP TABLE IF EXISTS public.kg_extraction_blocklist RESTRICT;
DROP TABLE IF EXISTS public.kg_node_chunks RESTRICT;
DROP TABLE IF EXISTS public.kg_links RESTRICT;
DROP TABLE IF EXISTS public.chat_messages RESTRICT;
DROP TABLE IF EXISTS public.summary_batch_items RESTRICT;
DROP TABLE IF EXISTS public.nexus_ingested_artifacts RESTRICT;
DROP TABLE IF EXISTS public.kg_usage_edges RESTRICT;
DROP TABLE IF EXISTS public.recompute_runs RESTRICT;
DROP TABLE IF EXISTS public.rag_sandbox_members RESTRICT;

-- Tier 2: depended on by Tier-1 tables that are now gone.
DROP TABLE IF EXISTS public.chat_sessions RESTRICT;
DROP TABLE IF EXISTS public.summary_batch_runs RESTRICT;
DROP TABLE IF EXISTS public.nexus_ingest_runs RESTRICT;
DROP TABLE IF EXISTS public.nexus_provider_accounts RESTRICT;
DROP TABLE IF EXISTS public.nexus_oauth_states RESTRICT;
DROP TABLE IF EXISTS public.rag_sandboxes RESTRICT;

-- Tier 3: kg_nodes — depended on by kg_links, kg_node_chunks, rag_sandbox_members.
DROP TABLE IF EXISTS public.kg_nodes RESTRICT;

-- Tier 4: kg_users — root of nearly every FK chain above.
DROP TABLE IF EXISTS public.kg_users RESTRICT;

-- Tier 5: _migrations_applied is independent.
DROP TABLE IF EXISTS public._migrations_applied RESTRICT;
