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

## Phase 7 hardening sweep (2026-05-10)

### 7.1 — `idx_retrieval_signal_workspace_target` dropped
Migration `_v2/27_drop_redundant_retrieval_idx.sql` applied. The 2-col index
was a strict subset of the Phase-1.A 3-col superset
`idx_retrieval_signal_workspace_class_target (workspace_id, query_class,
target_canonical_chunk_id) INCLUDE (source_canonical_chunk_id, weight)`.
Verified: `EXPLAIN` on `rag.search_signal_weights` workload picks
`Index Only Scan` on the superset index (cost 0.15..2.37, width=40).

### 7.2 — Legacy v1 RPC zombies dropped
Migration `_v2/28_drop_legacy_rpcs.sql` applied. 7 RPCs in `public.*` that
referenced Phase-6-dropped tables removed: `rag_resolve_entity_anchors`,
`rag_one_hop_neighbours`, `rag_fetch_anchor_seeds`, `rag_dense_recall`,
`rag_hybrid_search`, `rag_kasten_chunk_counts`, `rag_resolve_effective_nodes`.
Signatures captured live from `pg_proc` (the plan's signature guesses were
slightly wrong — `rag_dense_recall` takes `p_user_id`, not `p_workspace_id`).
**Out of scope:** 7 other `rag_*` RPCs in `public` still exist
(`rag_bandit_*`, `rag_bulk_add_to_sandbox`, `rag_kasten_node_frequencies`,
`rag_kasten_record_node_hit`, `rag_replace_node_chunks`,
`rag_subgraph_for_pagerank`) — touch only with explicit operator approval.

### 7.3 — Test-fixture cleanup hardening
- 7.3a (marker column): **SKIPPED** at current scale (~10-15 users). Email
  pattern + per-test fixture cleanup + sessionfinish hook is sufficient.
  Revisit at 1k users when fixture density makes pattern matching expensive.
- 7.3b: `pytest_sessionfinish` hook added to
  `tests/integration/v2/conftest.py`. End-of-session backstop sweeps any
  `e2e-[0-9a-f]{6,12}@test.com` user the per-test cleanup left behind.
  Best-effort; honours `SKIP_TEST_FIXTURE_SWEEP=1` opt-out.
  Verified: ran one live test, hook printed `swept 2 leftover test-fixture
  user(s)` (one from the failing teardown, one historical).
- 7.3c: nightly cron `cleanup-test-fixtures.yml` (03:17 UTC) runs
  `ops/scripts/purge_test_fixtures.py --age-hours 24`. Idempotent,
  service-role, scans up to 10k users. Dry-run verified locally
  (scanned=2, matched=0 with cutoff age=9999h).

### 7.4 — Audit of 6 retained `public.pricing_*` tables (no DROP)

Inspection of all six retained tables on 2026-05-10:

| table | cols | rows | v2 equivalent | code refs |
|---|---|---|---|---|
| `pricing_balances` | 5 | 0 | `billing.pricing_balances` | `06_backfill_billing.py` (copy source) |
| `pricing_disputes` | 12 | 0 | `billing.pricing_disputes` | `user_pricing/repository.py:609` (insert), backfill |
| `pricing_payment_events` | 6 | 0 | `billing.pricing_payment_events` | `user_pricing/repository.py:635,658` (insert), backfill |
| `pricing_plan_cache` | 6 | 0 | `billing.pricing_plan_cache` | `user_pricing/repository.py:494,513` (read+insert), backfill |
| `pricing_refunds` | 12 | 0 | `billing.pricing_refunds` | `user_pricing/repository.py:555` (insert), backfill |
| `pricing_webhook_events` | 9 | 0 | `billing.pricing_webhook_events` | backfill only |

**Status:** all six are zero-row but **still actively written by Razorpay
webhook code paths in `website/features/user_pricing/repository.py`**.
The KGRepository client they use (`repo._client.table(...)`) defaults to
the `public` schema with no explicit `.schema('billing')` selector. The
v2 backfill script `ops/scripts/refactor_v2/06_backfill_billing.py`
contains UNION queries against all six (lines 24-26) and INSERT-from
queries that copy `public.* -> billing.*` (lines 132, 140, 145, 151, 161,
171, 178), confirming the intended migration direction.

**Migration path before any DROP:**
1. Cut over the writer in `user_pricing/repository.py` to `.schema('billing').table(...)` (one-line change per call site, six call sites).
2. Run `06_backfill_billing.py` to backfill any in-flight rows from public to billing (today both are zero-row, so this is a one-shot drain).
3. Operator approval per occurrence (per CLAUDE.md "Pricing Module Authority" hard rules — pricing knobs are in the protected set).
4. Drop `public.pricing_{balances,disputes,payment_events,plan_cache,refunds,webhook_events}` in a single atomic Phase-7-bis migration with archive snapshot identical to Phase 6.

**Decision:** do NOT drop in Phase 7. Drops are gated on (1) the writer
cutover and (2) explicit operator authorisation. Surface as Phase 7-bis
candidate.

## Phase 8 closeout executed 2026-05-11

**Scope:** 12 commits (8.0.1 through 8.0-H10 + TX cross-tenant fix — see PURGE-COMPLETE-2026-05-11.md).

**Operator-approved overrides:**
- B-pricing-migration: refactor pricing module to billing.* schema (per pricing_module_authority memory).
- B+ atomic migration for T3: get_supabase_scope deleted; all 5 production callers (nexus + bulk_import + service.py) migrated in same task.
- 6 retained public.pricing_* tables + 6 RPCs DROP — APPLIED 2026-05-11 in `supabase/website/_v2/31_drop_legacy_pricing.sql` after pre-T6 audit confirmed zero live website refs.
- /api/me/avatar v1 → v2 port (closes operator-flagged H1 example) — done by other agent in 8704391.
- /api/graph/query + /api/graph/search → 410 Gone — done in fd6e2fd + 481c0aa.
- /api/graph/rebuild-links route deleted — done in 481c0aa.
- service.py atomic factory swap; consumers receive v2 client via lazy default — done in ddfc2c8.
- persist.py v1 helpers hard-deleted — done in ddfc2c8.
- website/core/supabase_kg/ directory delete — APPLIED 2026-05-11 (paired with the T6 DROP commit).
- T11 hybrid kg_features cleanup: 3 broken modules deleted; 2 pure-compute kept with CI allow-list (operator approved D-then-X scout).
- TX cross-tenant test fixture fix + UUID-leak assertion hardening per OWASP API1:2023 BOLA.

**Plan-amendment patches:**
- `feedback_deferral_is_a_decision.md` saved 2026-05-10 — counterpart to `feedback_anything_beyond_plan_needs_approval.md`.

**Post-closeout invariants (verified):**
- `git grep "from website.core.supabase_kg" -- "website/" "tests/"` → zero hits in live code paths after Phase 8.0.6 directory delete (2 live integration tests `importorskip`-gated; 6 ops/scripts annotated as LEGACY).
- `pytest tests/ -m "not live"` → 3 known flakes only.
- `pytest tests/integration/v2/ --live` → all pass (cross-tenant denial fixed in TX 73c2e35).
- Login verification: Naruto + Zoro PASS (to be re-verified in T15).

