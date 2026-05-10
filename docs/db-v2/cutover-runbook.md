# DB v2 Cutover Runbook

## Preconditions

- `.env.v2` contains `SUPABASE_V2_URL`, `SUPABASE_V2_ANON_KEY`, `SUPABASE_V2_SERVICE_ROLE_KEY`, `SUPABASE_V2_DATABASE_URL`, and `SUPABASE_V2_LISTEN_DATABASE_URL`.
- Supabase v2 API exposed schemas include `core, content, kg, rag, pipelines, billing`.
- Production PITR is enabled and a restore drill has been completed.
- Caddy maintenance mode has been tested.
- `python -m pytest tests/unit/ops/test_apply_migrations.py tests/unit/ops/test_apply_migrations_dsn.py tests/unit/ops/test_apply_migrations_v2.py tests/unit/supabase_v2 -q` passes.

## Steps

1. Enable maintenance mode.
2. Take/confirm Supabase PITR baseline timestamp.
3. Apply DB v2 files 00-09:
   `python ops/scripts/apply_migrations.py --v2 --target=v2-dev --update-manifest`
4. Run pg_partman maintenance if needed.
5. Run backfill with gates:
   `python ops/scripts/refactor_v2/00_full_backfill.py --continue`
6. Verify backfill:
   `python ops/scripts/refactor_v2/verify_backfill.py --continue`
7. Apply HNSW after rows land:
   `psql "$SUPABASE_V2_DATABASE_URL" -f supabase/website/_v2/10_hnsw_indexes.sql`
8. Set app env `DB_SCHEMA_VERSION=v2` and deploy.
9. Warm `/api/health/warm`.
10. Smoke test summarize, graph, pricing, and RAG chat.
11. Disable maintenance mode.

## Go/No-Go Checks

- `content.canonical_zettels` count is non-zero.
- `content.canonical_chunks` count is non-zero.
- `content.workspace_chunk_membership` has no orphan chunk/workspace references.
- `rag.retrieval_pipeline_config` has all required scorers for `prod`.
- `content.search_chunks` succeeds for an authorized JWT and fails for unauthorized workspace.
- Direct SELECT from canonical chunks is denied to authenticated clients.

## Phase 6 cutover executed 2026-05-10 (final, corrected)

**Operator-approved per-occurrence overrides:**
- 14-day soak guard bypassed (chat 2026-05-10).
- 30-table DROP list (22 originals + 3 views + 5 legacy `public.pricing_*` — derivative authorisation).

**Pre-flight pg_depend (now 3-branch: direct + pg_rewrite + pg_constraint):** 0 unexpected dependents.

**Tables/views dropped:** 22 originals + 3 views + 5 legacy pricing = 30 total.

**Migration recorded:** `_v2/15_drop_legacy_tables.sql` in `core._migrations_applied`
(checksum `aaadc27a25799366cb0caff8c8c17aa625c408d9116da5c80cc414945b7c940d`,
applied_at 2026-05-10T06:42:01Z, runner `phase6_drop_legacy.py`).

**Post-DROP verification:**
- `public.*` legacy: 0 tables / 0 views remain (info_schema.tables matchers + info_schema.views both = 0).
- `billing.*` (v2 pricing): row counts unchanged across all 11 tables vs pre-DROP baseline.
- 6 retained `public.pricing_*` tables (balances, disputes, payment_events, plan_cache, refunds, webhook_events) intact — outside the 5-table drop authorisation.
- Login verification: Naruto PASS, Zoro PASS (sign_in_with_password via v2 anon client).

**Plan-amendment patches included:**
- R2.5 pre-flight extended with pg_rewrite UNION (catches view deps).
- R2.5 pre-flight extended with pg_constraint UNION (catches FK deps — 5 pricing_* -> kg_users edges visible only via this branch).
- Allow-list grew from 22 -> 30 entries.
- DROP order topologically re-sorted: views/mview -> FK-leaves (incl. 5 pricing_*) -> tier-2 (chat_sessions, summary_batch_runs, nexus_*, rag_sandboxes) -> kg_nodes -> kg_users -> _migrations_applied.

**Archive extended:** `docs/db-v2/legacy-archive-2026-05-10.json`
- `tables.public.pricing_*` (5 entries) — full row dumps.
- `view_definitions.public.{kg_graph_view,kg_user_stats,rag_sandbox_stats}` — full pg_get_viewdef SQL.
- `phase6_predrop_counts.objects` — kind+count for all 30 objects (e.g. `_migrations_applied`=21, `kg_users`=1, `pricing_usage_counters`=8, all others 0).
- `phase6_predrop_counts.billing_baseline` — billing.* baseline for the post-DROP delta check.

