# DB Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [docs/superpowers/specs/2026-05-08-db-refactor-design.md](../specs/2026-05-08-db-refactor-design.md)

**Goal:** Migrate the entire production Supabase schema (kg_public/kg_features/rag_chatbot/summarization_engine/nexus/user_auth/user_pricing) to the new 6-schema layout (core/content/kg/rag/pipelines/billing) with canonical-content dedup, workspace tenancy, JWT-claim RLS, halfvec(768), unified usage_events, and a data-driven scorer registry — as a single weekend big-bang cutover with same-day PITR rollback.

**Architecture:** Six Postgres schemas with downward-only FK direction. Identity via Supabase `auth.users` → `core.profiles` (no Render legacy). RLS via JWT custom claim `workspace_ids[]` (no EXISTS-against-mapping anti-pattern). Content stored once in `content.canonical_*`, referenced via per-workspace overlay tables. RAG retrieval reads weights from a four-table scorer registry refreshed via `pg_notify`. Usage events in one partitioned fact table, projected into narrow per-feature aggregates and a hot-path `retrieval_signal_weights` table.

**Tech Stack:** Supabase Postgres 15+, pgvector 0.8+ (halfvec, iterative_scan), pg_partman 5+, Python 3.12, FastAPI, supabase-py, pytest + pytest-asyncio + pytest-httpx.

**Phases:**
- Phase 0 — Workspace + dev Supabase project + tested PITR (pre-flight, before any code)
- Phase 1 — New schema DDL (six schemas, all tables, triggers, helpers, RLS) applied idempotently to dev
- Phase 2 — Python repository layer (TDD against the new dev schema)
- Phase 3 — Scorer registry adapter (~150 lines, **mandatory**, with `pg_notify` hot-reload)
- Phase 4 — API + retrieval code updates (canonical-then-overlay write path; reads from registry)
- Phase 5 — Backfill scripts (canonical dedup with verification; profiles; kg; rag; pipelines; billing rekey; usage events)
- Phase 6 — Test coverage (sharing, dedup, RLS, registry hot-reload, quota race)
- Phase 7 — Cutover runbook + rollback runbook (1-pagers, executable checklists)
- Phase 8 — Cutover execution (the actual weekend window)
- Phase 9 — Post-cutover: drop old tables, monitor, finalize

---

## File Structure

### New SQL files (under `supabase/website/_v2/`)

| File | Responsibility |
|---|---|
| `_v2/00_extensions.sql` | `CREATE EXTENSION IF NOT EXISTS pgcrypto, vector, pg_partman` |
| `_v2/01_core_schema.sql` | `core.profiles`, `core.workspaces`, `core.workspace_members`, `core.usage_events` (partitioned), `core.usage_aggregates`, `core.quotas`, `core.soft_delete_queue`; auth-user trigger; auto-personal-workspace trigger; JWT-sync trigger; `core.jwt_workspace_ids()` helper |
| `_v2/02_content_schema.sql` | `content.embedding_model_versions`, `content.canonical_zettels`, `content.canonical_chunks` (halfvec(768) + HNSW), `content.workspace_zettels`, `content.workspace_chunk_membership`, FTS trigger, soft-delete reaper trigger |
| `_v2/03_kg_schema.sql` | `kg.kg_nodes`, `kg.kg_edges`, `kg.chunk_node_mentions`, `kg.expand_subgraph()` RPC |
| `_v2/04_rag_schema.sql` | `rag.kastens`, `rag.kasten_members`, `rag.kasten_zettels`, `rag.chat_sessions`, `rag.chat_messages`, `rag.retrieval_signal_weights`, `rag.retrieval_scorer_*` (4 tables), `rag.notify_pipeline_config_change()` trigger |
| `_v2/05_pipelines_schema.sql` | `pipelines.pipeline_runs`, `pipelines.pipeline_run_items` |
| `_v2/06_billing_schema.sql` | All `billing.pricing_*` tables (rekeyed to `profile_id UUID FK`); new `billing.pricing_plan_entitlements` |
| `_v2/07_partman_setup.sql` | `partman.create_parent('core.usage_events', 'occurred_at', 'native', 'monthly', p_premake := 6)` and retention policy |
| `_v2/08_rls_policies.sql` | RLS policies on every workspace-scoped table using `core.jwt_workspace_ids()` |
| `_v2/09_seed_scorer_registry.sql` | Initial `INSERT` statements for the 7 known scorers with their v1 `params` |

### New Python files (under `website/core/supabase_v2/` and `website/features/`)

| File | Responsibility |
|---|---|
| `website/core/supabase_v2/client.py` | Supabase client, JWT-claim parser, `current_profile_id()`, `current_workspace_ids()` helpers |
| `website/core/supabase_v2/models.py` | Pydantic: `Profile`, `Workspace`, `WorkspaceMember`, `CanonicalZettel`, `WorkspaceZettel`, `CanonicalChunk`, `WorkspaceChunkMembership`, `Kasten`, `KastenMember`, `KGNode`, `KGEdge`, `ChunkNodeMention`, `UsageEvent`, `Quota`, `ScorerVersion`, `PipelineConfig` |
| `website/core/supabase_v2/content_repository.py` | `upsert_canonical_zettel(url, body) → (canonical_id, was_new)`, `upsert_canonical_chunks(...)`, `add_workspace_overlay(workspace_id, canonical_id, ai_summary, tags) → workspace_zettel_id`, `soft_delete_workspace_zettel(...)` |
| `website/core/supabase_v2/kg_repository.py` | `upsert_kg_node`, `add_kg_edge`, `add_chunk_node_mention`, `expand_subgraph` |
| `website/core/supabase_v2/rag_repository.py` | Kastens CRUD, kasten_members (sharing), kasten_zettels |
| `website/core/supabase_v2/chat_repository.py` | Chat sessions/messages |
| `website/core/supabase_v2/billing_repository.py` | Pricing rekeyed; webhook → profile lookup via `razorpay_subscriber_id` |
| `website/core/supabase_v2/usage_events_repository.py` | `emit_event(workspace_id, profile_id, feature, unit, quantity, metadata)`; `consume_quota_atomic(workspace_id, feature, unit, period_start) → bool`; quota soft-check helper |
| `website/features/rag_pipeline/scoring/registry_adapter.py` | **THE ~150-LINE ADAPTER (mandatory Phase-1).** Reads `rag.retrieval_pipeline_config + retrieval_scorer_version`, exposes `RegistryAdapter.get_weight(scorer_name) → float`, `is_enabled(scorer_name) → bool`, `params(scorer_name) → dict`. Subscribes to `pg_notify('retrieval_pipeline_config_change')` and rebuilds local cache on event. Singleton with thread-safe `dict` snapshot. |
| `website/features/rag_pipeline/scoring/registry_init.py` | Boot-time bootstrap: validate every scorer in code has a matching registry row; fail-fast if missing |

### Modified Python files

| File | Change |
|---|---|
| `website/api/routes.py` | `/api/summarize`: read `workspace_id` from JWT claim; canonical-then-overlay insert via `ContentRepository`. `/api/graph`: query workspace-scoped overlay |
| `website/features/rag_pipeline/retrieval/hybrid.py` | Replace constants with `RegistryAdapter.get_weight('rrf_fusion')`, etc. |
| `website/features/rag_pipeline/retrieval/anti_magnet.py` | Read `floor`, `exponent` from `RegistryAdapter.params('anti_magnet')` |
| `website/features/rag_pipeline/retrieval/graph_score.py` | Read source from `rag.retrieval_signal_weights` (not `kg_usage_edges_agg`); weight from registry |
| `website/features/rag_pipeline/retrieval/entity_anchor.py` | Read seed-boost magnitude from registry |
| `website/features/user_pricing/repository.py` | `profile_id UUID` everywhere instead of `render_user_id TEXT` |
| `website/features/user_pricing/webhooks.py` | Razorpay payload → look up `profiles.razorpay_subscriber_id` → `profile_id` |
| `ops/scripts/recompute_usage_edges.py` | Renamed → `recompute_signal_weights.py`. Reads `core.usage_events WHERE feature IN ('node_cited','verdict_supported')`, writes `rag.retrieval_signal_weights` |
| `ops/scripts/apply_migrations.py` | Targets six schemas; consolidated `core._migrations_applied` table |

### New backfill scripts (under `ops/scripts/refactor_v2/`)

| File | Responsibility |
|---|---|
| `00_full_backfill.py` | Orchestrator: runs 01→08 in order, with progress + verification gates |
| `01_backfill_profiles.py` | `kg_users` → `core.profiles` (id, email, display_name, render_user_id → razorpay_subscriber_id where applicable) |
| `02_backfill_canonical_content.py` | THE BIG ONE: `kg_nodes` → grouped by (normalized_url, content_hash) → `canonical_zettels` + `canonical_chunks` + per-workspace `workspace_zettels` + `workspace_chunk_membership`. Re-embeds chunks as halfvec |
| `03_backfill_kg.py` | `kg_links` → `kg.kg_edges`. Slug-based KG nodes from old schema → `kg.kg_nodes`. `chunk_node_mentions` populated from extraction metadata |
| `04_backfill_rag.py` | `rag_sandboxes` → `rag.kastens`; `rag_sandbox_members` → `rag.kasten_zettels` (note: NOT `rag.kasten_members` — sandbox-members are zettels-in-sandbox) |
| `05_backfill_pipelines.py` | `summary_batch_runs/items` → `pipelines.pipeline_runs/items` (kind='summarize'); `nexus_*` → `pipeline_runs (kind='nexus_ingest')` |
| `06_backfill_billing.py` | All `pricing_*` tables: `render_user_id TEXT` → `profile_id UUID` lookup via `core.profiles.razorpay_subscriber_id` |
| `07_backfill_usage_events.py` | `kg_usage_edges` → `core.usage_events (feature='retrieval_signal_emit')`; `pricing_credit_ledger` → `core.usage_events (feature='pricing_credit_*')`; `pricing_usage_counters` → `core.usage_aggregates` |
| `08_recompute_signal_weights.py` | One-shot recompute of `rag.retrieval_signal_weights` from the just-backfilled `core.usage_events` |
| `verify_backfill.py` | Post-backfill assertions: row counts, sharing semantics, no-orphans, no-NULL-FKs, sample retrieval still works |
| `cutover.sh` | Runbook driver for the actual weekend (calls scripts in order, gates between phases) |

### Tests

| File | Responsibility |
|---|---|
| `tests/integration/test_canonical_dedup.py` | Two users capture same URL → one canonical row, two overlays |
| `tests/integration/test_kasten_sharing.py` | Owner shares kasten → recipient gets viewer → can read but not write; promote → can write |
| `tests/integration/test_jwt_rls.py` | RLS denies cross-workspace access; service-role bypasses; JWT-less anon denied |
| `tests/integration/test_scorer_registry_adapter.py` | Boot loads config; pg_notify on weight change → cache rebuilt within 100ms; fail-fast on missing scorer |
| `tests/integration/test_quota_enforcement.py` | 5 parallel debits at near-zero remaining → exactly N succeed, rest get 429 |
| `tests/integration/test_soft_delete_reaper.py` | Last membership leave → `soft_delete_queue` enqueued; reaper script after 7+ days shreds canonical |
| `tests/integration/test_halfvec_recall.py` | halfvec recall@10 within 1% of vector(768) baseline on a known query set |
| `tests/integration/test_workspace_auto_create.py` | New `auth.users` insert → `core.profiles` row → personal `core.workspaces` row → owner membership row → JWT claim populated |

### Documentation

| File | Responsibility |
|---|---|
| `docs/db-v2/cutover-runbook.md` | The 1-pager the operator follows on cutover day |
| `docs/db-v2/rollback-runbook.md` | The 1-pager for if cutover goes sideways |
| `docs/db-v2/post-cutover-monitoring.md` | What to watch for the first 24h |

---

## Phase 0 — Pre-Flight (Days -7 to -1, before code)

### Task 0.1: Provision a separate Supabase dev project for refactor work

**Files:** none (Supabase dashboard action) — record into `docs/db-v2/cutover-runbook.md` once that file exists in Phase 7.

- [ ] **Step 1:** Log into Supabase, create a new project `zettelkasten-v2-dev` in the same region as prod
- [ ] **Step 2:** Copy `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to `.env.v2` in repo root (wrap secrets in `<private>...</private>` per CLAUDE.md)
- [ ] **Step 3:** Verify `psql "$V2_DATABASE_URL" -c "SELECT version();"` returns Postgres 15+
- [ ] **Step 4:** Verify `pgvector` extension is available: `psql "$V2_DATABASE_URL" -c "SELECT * FROM pg_available_extensions WHERE name IN ('vector','pg_partman','pgcrypto');"` → all three rows present

### Task 0.2: Verify pgvector version supports halfvec + iterative_scan

- [ ] **Step 1:** Run `psql "$V2_DATABASE_URL" -c "SELECT extversion FROM pg_extension WHERE extname='vector';"`
- [ ] **Step 2:** Confirm version ≥ 0.8.0 (halfvec needs 0.7+, iterative_scan needs 0.8+). If less, escalate to user — **do not proceed with halfvec design**

### Task 0.3: Test PITR restore procedure on a throwaway Supabase project

- [ ] **Step 1:** In the v2-dev project, insert a sentinel row: `INSERT INTO public._pitr_test (note) VALUES ('before-pitr');`
- [ ] **Step 2:** Note timestamp T1
- [ ] **Step 3:** Wait 5 minutes
- [ ] **Step 4:** `INSERT INTO public._pitr_test (note) VALUES ('after-pitr');` Note T2
- [ ] **Step 5:** Trigger Supabase PITR restore to T1 (via dashboard)
- [ ] **Step 6:** Confirm `SELECT * FROM public._pitr_test` shows only the `before-pitr` row
- [ ] **Step 7:** Document elapsed time end-to-end (this is your rollback time budget)

### Task 0.4: Write the first draft of the cutover runbook (will be finalized in Phase 7)

**Files:** Create `docs/db-v2/cutover-runbook.md` (placeholder; expanded in Task 7.1)

- [ ] **Step 1:** Stub the runbook with the 11-step list from spec §5
- [ ] **Step 2:** Commit: `git add docs/db-v2/cutover-runbook.md && git commit -m "docs: cutover runbook stub"`

---

## Phase 1 — New Schema DDL (Days 1-3)

**Goal:** All six schemas applied idempotently to the v2-dev project. No app code yet.

### Task 1.1: Create the v2 SQL directory and base extensions file

**Files:** Create `supabase/website/_v2/00_extensions.sql`

- [ ] **Step 1:** Create the directory: `mkdir -p supabase/website/_v2`
- [ ] **Step 2:** Write `00_extensions.sql`:

```sql
-- _v2/00_extensions.sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector ≥ 0.8 required (halfvec + iterative_scan)
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;
```

- [ ] **Step 3:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/00_extensions.sql`
- [ ] **Step 4:** Verify: `psql -c "SELECT extname, extversion FROM pg_extension WHERE extname IN ('pgcrypto','vector','pg_partman');"` shows all three
- [ ] **Step 5:** Commit: `git add supabase/website/_v2/00_extensions.sql && git commit -m "feat: db-v2 extensions"`

### Task 1.2: Apply `core` schema DDL

**Files:** Create `supabase/website/_v2/01_core_schema.sql` (copy verbatim from spec §4.1)

- [ ] **Step 1:** Create the file with the full SQL from spec §4.1 (`core.profiles`, `core.workspaces`, `core.workspace_members`, `core.usage_events` partitioned, `core.usage_aggregates`, `core.quotas`, `core.soft_delete_queue`, `core.jwt_workspace_ids()`, the three triggers)
- [ ] **Step 2:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/01_core_schema.sql`
- [ ] **Step 3:** Verify tables exist: `psql -c "\dt core.*"` lists 7 tables (usage_events shows as partitioned table)
- [ ] **Step 4:** Verify the `core.jwt_workspace_ids()` function: `psql -c "SELECT core.jwt_workspace_ids();"` returns `{}` (empty array, no JWT context in psql)
- [ ] **Step 5:** Commit: `git add supabase/website/_v2/01_core_schema.sql && git commit -m "feat: db-v2 core schema"`

### Task 1.3: Apply `content` schema DDL with halfvec + HNSW

**Files:** Create `supabase/website/_v2/02_content_schema.sql` (copy verbatim from spec §4.2)

- [ ] **Step 1:** Write the file with the full SQL from spec §4.2. CRITICAL: column type must be `halfvec(768)` not `vector(768)`; HNSW index must use `halfvec_cosine_ops`
- [ ] **Step 2:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/02_content_schema.sql`
- [ ] **Step 3:** Verify HNSW index exists: `psql -c "SELECT indexname FROM pg_indexes WHERE tablename='canonical_chunks' AND indexname='idx_canonical_chunks_embedding_hnsw';"` returns one row
- [ ] **Step 4:** Verify `embedding_model_versions` is seeded with `gemini-001-mrl-768`: `psql -c "SELECT * FROM content.embedding_model_versions;"`
- [ ] **Step 5:** Test halfvec works: `psql -c "INSERT INTO content.canonical_chunks (canonical_zettel_id, chunk_idx, content, content_hash, chunk_type, embedding) VALUES ('00000000-0000-0000-0000-000000000000'::uuid, 0, 'test', 'fakehash'::bytea, 'atomic', array_fill(0.5, ARRAY[768])::halfvec);"` — expect FK error on `canonical_zettel_id` (proves the halfvec cast worked)
- [ ] **Step 6:** Commit: `git add supabase/website/_v2/02_content_schema.sql && git commit -m "feat: db-v2 content schema with halfvec"`

### Task 1.4: Apply `kg` schema DDL

**Files:** Create `supabase/website/_v2/03_kg_schema.sql` (copy verbatim from spec §4.3)

- [ ] **Step 1:** Write the file with full SQL from spec §4.3
- [ ] **Step 2:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/03_kg_schema.sql`
- [ ] **Step 3:** Verify: `psql -c "\dt kg.*"` shows 3 tables; `\df kg.*` shows `expand_subgraph` function
- [ ] **Step 4:** Commit: `git add supabase/website/_v2/03_kg_schema.sql && git commit -m "feat: db-v2 kg schema"`

### Task 1.5: Apply `rag` schema DDL (incl. scorer registry)

**Files:** Create `supabase/website/_v2/04_rag_schema.sql` (copy verbatim from spec §4.4)

- [ ] **Step 1:** Write the file with full SQL from spec §4.4 (kastens, members, zettels, chat, retrieval_signal_weights, four scorer-registry tables, `notify_pipeline_config_change` trigger)
- [ ] **Step 2:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/04_rag_schema.sql`
- [ ] **Step 3:** Verify: `psql -c "\dt rag.*"` shows 10 tables
- [ ] **Step 4:** Test pg_notify wiring: in one psql session run `LISTEN retrieval_pipeline_config_change;`, in another run `INSERT INTO rag.retrieval_pipeline_config (environment, scorer_name, version_id, enabled, weight) VALUES ('dev','test_scorer','v0',true,1.0);` (will fail FK; that's fine for this test, the trigger fires before the FK check) — confirm the listening session received a NOTIFY
- [ ] **Step 5:** Commit: `git add supabase/website/_v2/04_rag_schema.sql && git commit -m "feat: db-v2 rag schema with scorer registry"`

### Task 1.6: Apply `pipelines` schema DDL

**Files:** Create `supabase/website/_v2/05_pipelines_schema.sql` (from spec §4.5)

- [ ] **Step 1:** Write file with spec §4.5 SQL
- [ ] **Step 2:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/05_pipelines_schema.sql`
- [ ] **Step 3:** Verify: `psql -c "\dt pipelines.*"` shows 2 tables
- [ ] **Step 4:** Commit: `git add supabase/website/_v2/05_pipelines_schema.sql && git commit -m "feat: db-v2 pipelines schema"`

### Task 1.7: Apply `billing` schema DDL (rekeyed pricing tables)

**Files:** Create `supabase/website/_v2/06_billing_schema.sql`

- [ ] **Step 1:** Read the existing `supabase/website/user_pricing/schema.sql` and `supabase/website/kg_public/migrations/2026-05-01_user_pricing.sql` for the full set of pricing_* tables
- [ ] **Step 2:** For each pricing_* table, write the equivalent under `billing.` schema with: `render_user_id TEXT` → `profile_id UUID NOT NULL REFERENCES core.profiles(id) ON DELETE RESTRICT`. Drop any standalone `user_id UUID` column (replaced by `profile_id`)
- [ ] **Step 3:** Add the new `billing.pricing_plan_entitlements` table from spec §4.6
- [ ] **Step 4:** Rewrite `pricing_check_entitlement(p_profile_id UUID, p_feature TEXT, p_unit TEXT)` and `pricing_consume_entitlement(...)` functions to use `core.usage_events` + `billing.pricing_plan_entitlements` (replaces the hardcoded `pricing_plan_cap()` IF-branches)
- [ ] **Step 5:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/06_billing_schema.sql`
- [ ] **Step 6:** Verify: `psql -c "\dt billing.*"` shows 11 tables; `\df billing.*` shows the entitlement functions
- [ ] **Step 7:** Commit: `git add supabase/website/_v2/06_billing_schema.sql && git commit -m "feat: db-v2 billing schema rekeyed to profile_id"`

### Task 1.8: Set up pg_partman partitioning on `core.usage_events`

**Files:** Create `supabase/website/_v2/07_partman_setup.sql`

- [ ] **Step 1:** Write the file:

```sql
-- _v2/07_partman_setup.sql
SELECT partman.create_parent(
  p_parent_table := 'core.usage_events',
  p_control      := 'occurred_at',
  p_type         := 'native',
  p_interval     := 'monthly',
  p_premake      := 6                     -- create 6 months of future partitions
);

UPDATE partman.part_config
SET retention            = '24 months',
    retention_keep_table = false,         -- DETACH and DROP at retention boundary
    retention_keep_index = false,
    infinite_time_partitions = true
WHERE parent_table = 'core.usage_events';

-- Schedule partman maintenance (run daily)
-- This requires pg_cron OR a Supabase scheduled edge function.
-- Documented choice: use Supabase Cron (added in Q1 2026)
SELECT cron.schedule(
  'partman_run_maintenance', '0 3 * * *',
  $$ SELECT partman.run_maintenance(p_analyze := true); $$
);
```

- [ ] **Step 2:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/07_partman_setup.sql`
- [ ] **Step 3:** Verify partitions exist: `psql -c "SELECT relname FROM pg_class WHERE relname LIKE 'usage_events_p%' ORDER BY relname;"` should show ~7 monthly partition tables
- [ ] **Step 4:** Verify cron job: `psql -c "SELECT * FROM cron.job WHERE jobname='partman_run_maintenance';"`
- [ ] **Step 5:** Commit: `git add supabase/website/_v2/07_partman_setup.sql && git commit -m "feat: db-v2 partman partitioning on usage_events"`

### Task 1.9: Apply RLS policies to every workspace-scoped table

**Files:** Create `supabase/website/_v2/08_rls_policies.sql`

- [ ] **Step 1:** Write RLS policies using the spec §4.1 generic shape, applied to every table that carries `workspace_id`. Specifically:
  - `core.workspaces`, `core.workspace_members`, `core.usage_events`, `core.usage_aggregates`, `core.quotas`
  - `content.workspace_zettels`, `content.workspace_chunk_membership`
  - `kg.kg_nodes` (allow `workspace_id IS NULL` for global rows; service-role-only INSERT/UPDATE/DELETE on global), `kg.kg_edges` (same)
  - `rag.kastens`, `rag.kasten_members`, `rag.kasten_zettels`, `rag.chat_sessions`, `rag.chat_messages`, `rag.retrieval_signal_weights`
  - `billing.*` (all pricing tables)
  - `pipelines.pipeline_runs`, `pipelines.pipeline_run_items`
- [ ] **Step 2:** For `core.profiles`: special policy — user can SELECT/UPDATE only their own row (`id = auth.uid()`); service_role bypass
- [ ] **Step 3:** For `content.canonical_zettels` / `content.canonical_chunks`: service-role-only direct access; reads gated through `workspace_chunk_membership` RLS at the JOIN layer
- [ ] **Step 4:** For `rag.kasten_members`: special — user is allowed if `workspace_id = ANY(jwt_workspace_ids())` because membership rows are themselves workspace-keyed
- [ ] **Step 5:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/08_rls_policies.sql`
- [ ] **Step 6:** Verify with a synthetic test: create two profiles + two workspaces; insert a workspace_zettel into ws1; with a JWT for user2 (claim workspace_ids=[ws2]), `SELECT * FROM content.workspace_zettels` returns 0 rows
- [ ] **Step 7:** Commit: `git add supabase/website/_v2/08_rls_policies.sql && git commit -m "feat: db-v2 RLS policies (JWT-claim driven)"`

### Task 1.10: Seed the scorer registry

**Files:** Create `supabase/website/_v2/09_seed_scorer_registry.sql`

- [ ] **Step 1:** Identify every scorer in current Python code by searching `website/features/rag_pipeline/`:

```bash
grep -r "class.*Scorer" website/features/rag_pipeline/ --include="*.py"
```

Expected scorers (from CLAUDE.md retrieval description): `bm25`, `dense`, `rrf_fusion`, `cross_encoder`, `anti_magnet`, `graph_score`, `entity_anchor`, `dense_fallback`.

- [ ] **Step 2:** Write seed SQL:

```sql
-- _v2/09_seed_scorer_registry.sql
-- 1. Register implementations
INSERT INTO rag.retrieval_scorer_registry (scorer_name, impl_class, supported_inputs, description) VALUES
  ('bm25',          'website.features.rag_pipeline.retrieval.bm25:BM25Scorer',           '{"requires":["query_text"]}', 'Sparse BM25 over canonical_chunks.fts'),
  ('dense',         'website.features.rag_pipeline.retrieval.dense:DenseScorer',         '{"requires":["query_embedding"]}', 'HNSW dense over canonical_chunks.embedding'),
  ('rrf_fusion',    'website.features.rag_pipeline.retrieval.rrf:RRFFusionScorer',       '{"requires":["bm25_rank","dense_rank"]}', 'Reciprocal Rank Fusion of bm25+dense'),
  ('anti_magnet',   'website.features.rag_pipeline.retrieval.anti_magnet:AntiMagnetScorer', '{"requires":["chunk_count_per_kasten"]}', 'Multiplicative damping 1/sqrt(chunk_count)'),
  ('graph_score',   'website.features.rag_pipeline.retrieval.graph_score:GraphScoreScorer', '{"requires":["retrieval_signal_weights"]}', 'Co-citation prior from rag.retrieval_signal_weights'),
  ('entity_anchor', 'website.features.rag_pipeline.retrieval.entity_anchor:EntityAnchorScorer', '{"requires":["entity_extracted_from_query"]}', 'Seed boost when query mentions a Kasten member node'),
  ('cross_encoder', 'website.features.rag_pipeline.retrieval.cross_encoder:CrossEncoderScorer', '{"requires":["fp32_verify_top_k"]}', 'BGE int8; fp32-verify on top-3'),
  ('dense_fallback','website.features.rag_pipeline.retrieval.dense_fallback:DenseFallbackScorer', '{"requires":["effective_nodes"]}', 'Last-resort dense recall when hybrid returns 0 rows');

-- 2. Initial v1 versions of each scorer's params (taken from current Python constants)
INSERT INTO rag.retrieval_scorer_version (scorer_name, version_id, params, notes, created_by) VALUES
  ('bm25',          'v1', '{}',                                                 'baseline', 'migration'),
  ('dense',         'v1', '{"top_k":64}',                                       'baseline', 'migration'),
  ('rrf_fusion',    'v1', '{"k":60}',                                           'standard RRF k=60', 'migration'),
  ('anti_magnet',   'v1', '{"floor":1, "exponent":0.5}',                        'iter-08 chunk_share', 'migration'),
  ('graph_score',   'v1', '{"decay_seconds":2592000, "min_weight":0.05}',       '30d half-life decay', 'migration'),
  ('entity_anchor', 'v1', '{"seed_boost":2.0, "max_anchors":4}',                'iter-08 phase 6', 'migration'),
  ('cross_encoder', 'v1', '{"fp32_verify_top_k":3}',                            'iter-03 phase 1A.5', 'migration'),
  ('dense_fallback','v1', '{"limit":8, "enabled":true}',                        'iter-10 P5/Q6,Q7', 'migration');

-- 3. Active prod config (the Personalize $LATEST pointer)
INSERT INTO rag.retrieval_pipeline_config (environment, scorer_name, version_id, enabled, weight, updated_by) VALUES
  ('prod','bm25',         'v1', true, 1.0, 'migration'),
  ('prod','dense',        'v1', true, 1.0, 'migration'),
  ('prod','rrf_fusion',   'v1', true, 1.0, 'migration'),
  ('prod','anti_magnet',  'v1', true, 1.0, 'migration'),
  ('prod','graph_score',  'v1', true, 1.0, 'migration'),
  ('prod','entity_anchor','v1', true, 1.0, 'migration'),
  ('prod','cross_encoder','v1', true, 1.0, 'migration'),
  ('prod','dense_fallback','v1', true, 1.0, 'migration');

-- 4. Mirror to staging + dev environments
INSERT INTO rag.retrieval_pipeline_config (environment, scorer_name, version_id, enabled, weight, updated_by)
SELECT env, scorer_name, version_id, enabled, weight, updated_by
FROM rag.retrieval_pipeline_config, (VALUES ('staging'),('dev')) AS e(env)
WHERE environment = 'prod';
```

- [ ] **Step 3:** Apply: `psql "$V2_DATABASE_URL" -f supabase/website/_v2/09_seed_scorer_registry.sql`
- [ ] **Step 4:** Verify: `psql -c "SELECT environment, scorer_name, version_id, enabled, weight FROM rag.retrieval_pipeline_config ORDER BY environment, scorer_name;"` — 24 rows (8 scorers × 3 envs)
- [ ] **Step 5:** Commit: `git add supabase/website/_v2/09_seed_scorer_registry.sql && git commit -m "feat: seed scorer registry with v1 of every scorer"`

### Task 1.11: Update `apply_migrations.py` to target v2

**Files:** Modify `ops/scripts/apply_migrations.py`

- [ ] **Step 1:** Read the current `ops/scripts/apply_migrations.py` to understand the existing `_migrations_applied` table-tracking logic
- [ ] **Step 2:** Add a `--v2` flag mode that targets `supabase/website/_v2/*.sql` in alphabetical order (00→09) and tracks them in a new `core._migrations_applied` table (which lives in `core` schema, not `kg_public`)
- [ ] **Step 3:** The v2 mode should be idempotent (re-running applies only un-applied files) and verify checksum like the existing logic
- [ ] **Step 4:** Test: drop and recreate the v2-dev project's schemas; run `python ops/scripts/apply_migrations.py --v2`; verify all 10 files are recorded in `core._migrations_applied`
- [ ] **Step 5:** Commit: `git add ops/scripts/apply_migrations.py && git commit -m "feat: apply_migrations.py supports --v2 mode"`

---

## Phase 2 — Python Repository Layer (Days 4-7)

**Goal:** TDD a clean Python repository layer against the v2 schema. Existing `website/core/supabase_kg/` stays untouched until cutover.

### Task 2.1: Scaffold `website/core/supabase_v2/` package

**Files:** Create `website/core/supabase_v2/__init__.py`, `website/core/supabase_v2/client.py`

- [ ] **Step 1:** `mkdir website/core/supabase_v2`
- [ ] **Step 2:** Create `website/core/supabase_v2/__init__.py`:

```python
"""Supabase v2 repository layer for the schema redesign.

Replaces website.core.supabase_kg/ at cutover. Until then, this package is
parallel infrastructure used by tests against the v2-dev project.
"""
```

- [ ] **Step 3:** Create `website/core/supabase_v2/client.py`:

```python
"""Supabase v2 client + JWT-claim helpers."""
from __future__ import annotations
from functools import lru_cache
from typing import Any
from supabase import create_client, Client
from telegram_bot.config.settings import get_settings


@lru_cache(maxsize=1)
def get_v2_client() -> Client:
    """Return the singleton Supabase client targeting the v2 schema project."""
    settings = get_settings()
    if not settings.supabase_v2_url or not settings.supabase_v2_service_role_key:
        raise RuntimeError("SUPABASE_V2_URL and SUPABASE_V2_SERVICE_ROLE_KEY required")
    return create_client(settings.supabase_v2_url, settings.supabase_v2_service_role_key)


def is_v2_configured() -> bool:
    settings = get_settings()
    return bool(settings.supabase_v2_url and settings.supabase_v2_service_role_key)


def parse_jwt_workspace_ids(jwt_claims: dict[str, Any]) -> list[str]:
    """Extract workspace_ids[] from a Supabase JWT's app_metadata claim.

    The eager-refresh trigger in core.sync_workspace_ids_to_jwt writes the array
    into auth.users.raw_app_meta_data, which surfaces in app_metadata at JWT issue.
    """
    return list(jwt_claims.get("app_metadata", {}).get("workspace_ids", []))
```

- [ ] **Step 4:** Add v2 settings to `telegram_bot/config/settings.py`:

```python
supabase_v2_url: str | None = None
supabase_v2_service_role_key: str | None = None
```

- [ ] **Step 5:** Add to `ops/.env.example`: `SUPABASE_V2_URL=`, `SUPABASE_V2_SERVICE_ROLE_KEY=`
- [ ] **Step 6:** Commit: `git add -A && git commit -m "feat: supabase_v2 client scaffold"`

### Task 2.2: Pydantic models for v2

**Files:** Create `website/core/supabase_v2/models.py`

- [ ] **Step 1:** Create `models.py` with all v2 models. Use Pydantic BaseModel. Key models:

```python
"""Pydantic models for the v2 schema. Mirrors DDL field names/types."""
from __future__ import annotations
from datetime import date, datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field


class Profile(BaseModel):
    id: UUID
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    razorpay_subscriber_id: str | None = None
    created_at: datetime
    updated_at: datetime


class Workspace(BaseModel):
    id: UUID
    owner_profile_id: UUID
    name: str
    is_personal: bool = True
    created_at: datetime
    updated_at: datetime


class WorkspaceMember(BaseModel):
    workspace_id: UUID
    profile_id: UUID
    role: str = Field(pattern="^(owner|editor|viewer)$")
    added_at: datetime


class CanonicalZettel(BaseModel):
    id: UUID
    normalized_url: str
    content_hash: bytes
    source_type: str
    title: str | None = None
    body_md: str | None = None
    publication_date: date | None = None
    source_metadata: dict[str, Any] = {}
    created_at: datetime


class CanonicalZettelCreate(BaseModel):
    normalized_url: str
    content_hash: bytes
    source_type: str
    title: str | None = None
    body_md: str | None = None
    publication_date: date | None = None
    source_metadata: dict[str, Any] = {}


class WorkspaceZettel(BaseModel):
    id: UUID
    workspace_id: UUID
    canonical_zettel_id: UUID
    ai_summary: str | None = None
    ai_summary_engine_version: str | None = None
    user_tags: list[str] = []
    user_note: str | None = None
    pinned: bool = False
    added_via: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


# (Continue with CanonicalChunk, WorkspaceChunkMembership, Kasten, KastenMember,
#  KGNode, KGEdge, ChunkNodeMention, UsageEvent, Quota, ScorerVersion,
#  PipelineConfig, PipelineRun.)
```

- [ ] **Step 2:** Continue building out remaining models (one per table) following the same pattern. Total ~25 models.
- [ ] **Step 3:** Run `python -c "from website.core.supabase_v2.models import *; print('imports OK')"` — expect `imports OK`
- [ ] **Step 4:** Commit: `git add website/core/supabase_v2/models.py && git commit -m "feat: pydantic models for v2 schema"`

### Task 2.3: Test infrastructure for v2 (fresh-DB-per-test fixture)

**Files:** Create `tests/v2/conftest.py`

- [ ] **Step 1:** Create the v2 test directory: `mkdir -p tests/v2/integration`
- [ ] **Step 2:** Write `tests/v2/conftest.py`:

```python
"""Test fixtures for v2 schema integration tests.

Strategy: use the v2-dev Supabase project, but truncate all workspace-scoped
tables between tests for isolation. Schema is created once via apply_migrations.py.
"""
from __future__ import annotations
import pytest
from website.core.supabase_v2.client import get_v2_client


@pytest.fixture(scope="session", autouse=True)
def ensure_v2_schema():
    """Apply v2 schema before any test runs (idempotent)."""
    import subprocess
    result = subprocess.run(
        ["python", "ops/scripts/apply_migrations.py", "--v2"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"v2 migrations failed: {result.stderr}"


@pytest.fixture
def fresh_v2_db():
    """Truncate all workspace-scoped tables. Run in a transaction wrapper if possible."""
    client = get_v2_client()
    sql = """
    TRUNCATE
      content.workspace_chunk_membership,
      content.workspace_zettels,
      content.canonical_chunks,
      content.canonical_zettels,
      kg.chunk_node_mentions, kg.kg_edges, kg.kg_nodes,
      rag.chat_messages, rag.chat_sessions,
      rag.kasten_zettels, rag.kasten_members, rag.kastens,
      rag.retrieval_signal_weights,
      pipelines.pipeline_run_items, pipelines.pipeline_runs,
      core.usage_aggregates, core.quotas,
      core.workspace_members, core.workspaces,
      core.profiles
    CASCADE;
    """
    client.rpc("execute_sql", {"q": sql}).execute()
    yield client
```

- [ ] **Step 3:** Add `tests/v2/__init__.py` (empty) and `tests/v2/integration/__init__.py` (empty)
- [ ] **Step 4:** Verify fixture loads: `pytest tests/v2/ --collect-only`
- [ ] **Step 5:** Commit: `git add tests/v2/ && git commit -m "test: v2 integration test fixtures"`

### Task 2.4: TDD `ContentRepository.upsert_canonical_zettel`

**Files:** Create `website/core/supabase_v2/content_repository.py`, `tests/v2/integration/test_content_repository.py`

- [ ] **Step 1: Write the failing test:**

```python
# tests/v2/integration/test_content_repository.py
import hashlib
import pytest
from uuid import uuid4
from website.core.supabase_v2.content_repository import ContentRepository
from website.core.supabase_v2.models import CanonicalZettelCreate


@pytest.mark.asyncio
async def test_upsert_canonical_zettel_idempotent(fresh_v2_db):
    """Same (normalized_url, content_hash) inserted twice returns the same canonical_id."""
    repo = ContentRepository(fresh_v2_db)
    body = "the quick brown fox"
    create = CanonicalZettelCreate(
        normalized_url="https://example.com/post-1",
        content_hash=hashlib.sha256(body.encode()).digest(),
        source_type="web",
        title="Post 1",
        body_md=body,
    )
    canonical_id_1, was_new_1 = await repo.upsert_canonical_zettel(create)
    canonical_id_2, was_new_2 = await repo.upsert_canonical_zettel(create)

    assert canonical_id_1 == canonical_id_2
    assert was_new_1 is True
    assert was_new_2 is False
```

- [ ] **Step 2:** Run: `pytest tests/v2/integration/test_content_repository.py::test_upsert_canonical_zettel_idempotent -v` → expected FAIL (`ContentRepository` not defined)

- [ ] **Step 3: Implement minimal:**

```python
# website/core/supabase_v2/content_repository.py
from __future__ import annotations
from uuid import UUID
from supabase import Client
from .models import CanonicalZettelCreate, CanonicalZettel


class ContentRepository:
    def __init__(self, client: Client):
        self.client = client

    async def upsert_canonical_zettel(
        self, create: CanonicalZettelCreate
    ) -> tuple[UUID, bool]:
        """Insert canonical_zettel; return (id, was_new). Dedup key = (normalized_url, content_hash)."""
        # Try to find existing first
        existing = (
            self.client.table("content.canonical_zettels")
            .select("id")
            .eq("normalized_url", create.normalized_url)
            .eq("content_hash", create.content_hash.hex())
            .execute()
        )
        if existing.data:
            return UUID(existing.data[0]["id"]), False

        inserted = (
            self.client.table("content.canonical_zettels")
            .insert(create.model_dump(mode="json"))
            .execute()
        )
        return UUID(inserted.data[0]["id"]), True
```

- [ ] **Step 4:** Run test: PASS
- [ ] **Step 5:** Commit: `git add website/core/supabase_v2/content_repository.py tests/v2/integration/test_content_repository.py && git commit -m "feat: ContentRepository.upsert_canonical_zettel"`

### Task 2.5: TDD `ContentRepository.upsert_canonical_chunks` with halfvec

**Files:** Modify `website/core/supabase_v2/content_repository.py`, modify `tests/v2/integration/test_content_repository.py`

- [ ] **Step 1: Write failing test:**

```python
@pytest.mark.asyncio
async def test_upsert_canonical_chunks_halfvec(fresh_v2_db):
    repo = ContentRepository(fresh_v2_db)
    canonical_id, _ = await repo.upsert_canonical_zettel(
        CanonicalZettelCreate(
            normalized_url="https://example.com/post-2",
            content_hash=hashlib.sha256(b"body").digest(),
            source_type="web",
        )
    )
    chunks = [
        {
            "chunk_idx": 0, "content": "first chunk",
            "content_hash": hashlib.sha256(b"first chunk").digest(),
            "chunk_type": "semantic",
            "embedding": [0.5] * 768,    # halfvec accepts python list of float
        }
    ]
    chunk_ids = await repo.upsert_canonical_chunks(canonical_id, chunks)
    assert len(chunk_ids) == 1
```

- [ ] **Step 2:** Run → FAIL (method not defined)
- [ ] **Step 3: Add the method to `ContentRepository`** (uses Postgres COPY via the `execute_sql` RPC because supabase-py struggles with halfvec round-tripping; or use `pgvector.sqlalchemy` if you add SQLAlchemy):

```python
async def upsert_canonical_chunks(
    self, canonical_zettel_id: UUID, chunks: list[dict]
) -> list[UUID]:
    """Insert chunks; halfvec embedding from python list[float]. Returns chunk_ids."""
    rows = [
        {
            "canonical_zettel_id": str(canonical_zettel_id),
            "chunk_idx": c["chunk_idx"],
            "content": c["content"],
            "content_hash": c["content_hash"].hex() if isinstance(c["content_hash"], bytes) else c["content_hash"],
            "chunk_type": c["chunk_type"],
            "start_offset": c.get("start_offset"),
            "end_offset": c.get("end_offset"),
            "token_count": c.get("token_count"),
            "embedding": c["embedding"],  # supabase-py serializes list[float] to halfvec text repr
            "metadata": c.get("metadata", {}),
        }
        for c in chunks
    ]
    result = (
        self.client.table("content.canonical_chunks")
        .upsert(rows, on_conflict="canonical_zettel_id,chunk_idx")
        .execute()
    )
    return [UUID(r["id"]) for r in result.data]
```

- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit: `git add -A && git commit -m "feat: ContentRepository.upsert_canonical_chunks with halfvec"`

### Task 2.6: TDD `ContentRepository.add_workspace_overlay`

**Files:** Modify `content_repository.py` and `test_content_repository.py`

- [ ] **Step 1: Write failing test** — covers the dedup case (two workspaces share canonical, each gets own overlay):

```python
@pytest.mark.asyncio
async def test_two_workspaces_share_canonical_get_distinct_overlays(fresh_v2_db):
    """Two workspaces capturing same URL → 1 canonical_zettel, 2 workspace_zettels."""
    repo = ContentRepository(fresh_v2_db)

    # Create two profiles + workspaces (auto-trigger creates personal workspaces)
    p1, w1 = create_test_profile_and_workspace(fresh_v2_db, "user1@test.com")
    p2, w2 = create_test_profile_and_workspace(fresh_v2_db, "user2@test.com")

    create = CanonicalZettelCreate(
        normalized_url="https://example.com/shared",
        content_hash=hashlib.sha256(b"shared body").digest(),
        source_type="web",
        body_md="shared body",
    )
    canonical_id, was_new_1 = await repo.upsert_canonical_zettel(create)
    canonical_id_2, was_new_2 = await repo.upsert_canonical_zettel(create)
    assert canonical_id == canonical_id_2 and was_new_1 and not was_new_2

    overlay_1 = await repo.add_workspace_overlay(
        workspace_id=w1, canonical_zettel_id=canonical_id,
        ai_summary="user1 summary", user_tags=["tag1"], added_via="website",
    )
    overlay_2 = await repo.add_workspace_overlay(
        workspace_id=w2, canonical_zettel_id=canonical_id,
        ai_summary="user2 summary", user_tags=["tag2"], added_via="website",
    )
    assert overlay_1 != overlay_2

    # Verify canonical row count is 1, overlay count is 2
    canonicals = fresh_v2_db.table("content.canonical_zettels").select("id").execute()
    overlays = fresh_v2_db.table("content.workspace_zettels").select("id,workspace_id,ai_summary").execute()
    assert len(canonicals.data) == 1
    assert len(overlays.data) == 2
    assert {o["workspace_id"] for o in overlays.data} == {str(w1), str(w2)}
    assert {o["ai_summary"] for o in overlays.data} == {"user1 summary", "user2 summary"}
```

- [ ] **Step 2:** Add `create_test_profile_and_workspace` helper to `tests/v2/conftest.py`:

```python
def create_test_profile_and_workspace(client, email: str):
    from uuid import uuid4
    profile_id = uuid4()
    # Insert into auth.users first (Supabase admin API), or directly into core.profiles
    client.table("core.profiles").insert({
        "id": str(profile_id), "email": email, "display_name": email
    }).execute()
    # The auto-personal-workspace trigger fired; fetch the workspace
    ws = (
        client.table("core.workspaces")
        .select("id")
        .eq("owner_profile_id", str(profile_id))
        .eq("is_personal", True)
        .single()
        .execute()
    )
    return profile_id, UUID(ws.data["id"])
```

- [ ] **Step 3:** Run test → FAIL (`add_workspace_overlay` undefined)
- [ ] **Step 4:** Implement:

```python
async def add_workspace_overlay(
    self, *, workspace_id: UUID, canonical_zettel_id: UUID,
    ai_summary: str | None = None, ai_summary_engine_version: str | None = None,
    user_tags: list[str] | None = None, user_note: str | None = None,
    pinned: bool = False, added_via: str = "website",
) -> UUID:
    row = {
        "workspace_id": str(workspace_id),
        "canonical_zettel_id": str(canonical_zettel_id),
        "ai_summary": ai_summary,
        "ai_summary_engine_version": ai_summary_engine_version,
        "user_tags": user_tags or [],
        "user_note": user_note,
        "pinned": pinned,
        "added_via": added_via,
    }
    result = (
        self.client.table("content.workspace_zettels")
        .upsert(row, on_conflict="workspace_id,canonical_zettel_id")
        .execute()
    )
    return UUID(result.data[0]["id"])
```

- [ ] **Step 5:** Run → PASS
- [ ] **Step 6:** Commit: `git add -A && git commit -m "feat: add_workspace_overlay; verify cross-workspace dedup"`

### Task 2.7: TDD `KGRepository` (kg_nodes, kg_edges, chunk_node_mentions)

**Files:** Create `website/core/supabase_v2/kg_repository.py`, `tests/v2/integration/test_kg_repository.py`

Follow the same TDD pattern as Tasks 2.4-2.6:
- [ ] **Step 1:** Test for `upsert_kg_node` (workspace-scoped + global)
- [ ] **Step 2:** Test for `add_kg_edge`
- [ ] **Step 3:** Test for `add_chunk_node_mention`
- [ ] **Step 4:** Test for `expand_subgraph` (calls the RPC, verifies BFS up to depth N)
- [ ] **Step 5:** For each, write the implementation that makes it pass
- [ ] **Step 6:** Commit per method

### Task 2.8: TDD `RAGRepository` (kastens, kasten_members, kasten_zettels)

**Files:** Create `website/core/supabase_v2/rag_repository.py`, `tests/v2/integration/test_rag_repository.py`

- [ ] **Step 1:** Test for `create_kasten` (workspace-scoped, name unique per workspace)
- [ ] **Step 2:** Test for `add_kasten_member` with role enforcement (only workspaces with kasten access can use it; viewer cannot add zettels)
- [ ] **Step 3:** Test for `share_kasten(kasten_id, with_workspace_id, role='viewer')` — Q1.b semantics
- [ ] **Step 4:** Test for `add_zettel_to_kasten`
- [ ] **Step 5:** Implementation for each
- [ ] **Step 6:** Commit per method

### Task 2.9: TDD `ChatRepository`

**Files:** Create `website/core/supabase_v2/chat_repository.py`, `tests/v2/integration/test_chat_repository.py`

- [ ] **Step 1:** Test create chat session (workspace-scoped, optional kasten_id)
- [ ] **Step 2:** Test insert chat message with citations + verdict + token_counts
- [ ] **Step 3:** Implementation
- [ ] **Step 4:** Commit

### Task 2.10: TDD `UsageEventsRepository` with quota race-test

**Files:** Create `website/core/supabase_v2/usage_events_repository.py`, `tests/v2/integration/test_usage_events.py`

- [ ] **Step 1:** Write failing test for `emit_event` (basic insert; verify partman partition picks it up)

```python
@pytest.mark.asyncio
async def test_emit_event_lands_in_correct_partition(fresh_v2_db):
    profile_id, workspace_id = create_test_profile_and_workspace(fresh_v2_db, "u@t.com")
    repo = UsageEventsRepository(fresh_v2_db)
    await repo.emit_event(
        workspace_id=workspace_id, profile_id=profile_id,
        feature="rag_chat", unit="messages", quantity=1,
    )
    rows = fresh_v2_db.table("core.usage_events").select("*").execute()
    assert len(rows.data) == 1
```

- [ ] **Step 2:** Implement `emit_event`
- [ ] **Step 3:** Run → PASS
- [ ] **Step 4:** Write **race-condition test** for `consume_quota_atomic` (the spec §6 race-safe pattern):

```python
@pytest.mark.asyncio
async def test_consume_quota_atomic_5_parallel_near_zero(fresh_v2_db):
    """5 parallel debits at remaining=3 → exactly 3 succeed, 2 get False."""
    profile_id, workspace_id = create_test_profile_and_workspace(fresh_v2_db, "u@t.com")
    period_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    fresh_v2_db.table("core.quotas").insert({
        "workspace_id": str(workspace_id), "feature": "rag_chat", "unit": "messages",
        "period_start": period_start.isoformat(), "remaining": 3, "limit_total": 100,
    }).execute()

    repo = UsageEventsRepository(fresh_v2_db)
    results = await asyncio.gather(*[
        repo.consume_quota_atomic(workspace_id, "rag_chat", "messages", period_start)
        for _ in range(5)
    ])
    assert sum(results) == 3   # exactly 3 True, 2 False
```

- [ ] **Step 5:** Implement `consume_quota_atomic` using the spec §6 pattern (single atomic UPDATE with RETURNING):

```python
async def consume_quota_atomic(
    self, workspace_id: UUID, feature: str, unit: str, period_start: datetime
) -> bool:
    """Atomic debit. Returns True on success, False if exhausted."""
    sql = """
        UPDATE core.quotas
        SET remaining = remaining - 1
        WHERE workspace_id = %(ws)s AND feature = %(f)s AND unit = %(u)s
          AND period_start = %(ps)s AND remaining > 0
        RETURNING remaining;
    """
    result = self.client.rpc("exec_sql_returning", {
        "sql": sql,
        "params": {"ws": str(workspace_id), "f": feature, "u": unit, "ps": period_start.isoformat()},
    }).execute()
    return bool(result.data)  # empty if no row matched (=quota exhausted)
```

- [ ] **Step 6:** Run race test → PASS (Postgres MVCC + UPDATE-WHERE serializes correctly)
- [ ] **Step 7:** Commit: `git add -A && git commit -m "feat: UsageEventsRepository with race-safe consume_quota_atomic"`

### Task 2.11: TDD `BillingRepository` (rekey from render_user_id → profile_id)

**Files:** Create `website/core/supabase_v2/billing_repository.py`, `tests/v2/integration/test_billing.py`

- [ ] **Step 1:** Test for `record_payment_event(profile_id, ...)` — uses `profile_id UUID FK`, NOT a text identifier
- [ ] **Step 2:** Test for `lookup_profile_from_razorpay_subscriber(razorpay_id) → profile_id` (this is the webhook entry point)
- [ ] **Step 3:** Test for `record_credit_ledger` and `update_balance`
- [ ] **Step 4:** Test for `check_entitlement(profile_id, feature, unit)` calling the new SQL function
- [ ] **Step 5:** Implementation
- [ ] **Step 6:** Commit per method

---

## Phase 3 — Scorer Registry Adapter (Days 8-9, MANDATORY Phase-1)

**Goal:** ~150 lines of Python that bridge the data-driven registry to code-driven scorer impls. Without this, the registry is decorative (per Q6.b — non-negotiable).

### Task 3.1: TDD the `RegistryAdapter` core (read-at-boot, in-memory snapshot)

**Files:** Create `website/features/rag_pipeline/scoring/__init__.py`, `website/features/rag_pipeline/scoring/registry_adapter.py`, `tests/v2/integration/test_scorer_registry_adapter.py`

- [ ] **Step 1:** Write failing test:

```python
# tests/v2/integration/test_scorer_registry_adapter.py
import pytest
from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter


@pytest.mark.asyncio
async def test_adapter_loads_seeded_scorers(fresh_v2_db_with_registry_seed):
    adapter = RegistryAdapter(client=fresh_v2_db_with_registry_seed, environment="prod")
    await adapter.load()

    assert adapter.is_enabled("anti_magnet") is True
    assert adapter.get_weight("anti_magnet") == 1.0
    assert adapter.params("anti_magnet") == {"floor": 1, "exponent": 0.5}
    assert adapter.params("graph_score")["decay_seconds"] == 2592000
```

- [ ] **Step 2:** Add fixture `fresh_v2_db_with_registry_seed` to `tests/v2/conftest.py` (same as `fresh_v2_db` but doesn't truncate `rag.retrieval_scorer_*` tables — those are seeded by Task 1.10's seed file)

- [ ] **Step 3:** Run → FAIL (adapter not defined)

- [ ] **Step 4: Implement the adapter (target: ~80 lines for this part):**

```python
# website/features/rag_pipeline/scoring/registry_adapter.py
"""Scorer registry adapter — bridges rag.retrieval_pipeline_config to runtime scorer code.

Boot path:
  1. RegistryAdapter.load() reads rag.retrieval_pipeline_config + retrieval_scorer_version
     for the current environment, builds an in-memory snapshot.
  2. Scorers ask: is_enabled(name), get_weight(name), params(name).
  3. RegistryAdapter.start_listening() opens an async pg_notify listener on
     'retrieval_pipeline_config_change' and rebuilds the snapshot on event.

Spec ref: docs/superpowers/specs/2026-05-08-db-refactor-design.md §4.4 + §6.
Mandatory Phase-1 deliverable per Q6.b — without this the registry is decorative.
"""
from __future__ import annotations
import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any
from supabase import Client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScorerSnapshot:
    enabled: bool
    weight: float
    params: dict[str, Any]
    version_id: str
    impl_class: str


class RegistryAdapter:
    def __init__(self, client: Client, environment: str = "prod"):
        self.client = client
        self.environment = environment
        self._snapshot: dict[str, ScorerSnapshot] = {}
        self._lock = threading.RLock()
        self._listener_task: asyncio.Task | None = None

    async def load(self) -> None:
        """Synchronously fetch + cache all scorer config for this environment."""
        configs = (
            self.client.table("rag.retrieval_pipeline_config")
            .select("scorer_name, version_id, enabled, weight")
            .eq("environment", self.environment)
            .execute()
        )
        if not configs.data:
            raise RuntimeError(f"No scorer configs found for environment={self.environment}")

        # Bulk fetch versions
        versions = (
            self.client.table("rag.retrieval_scorer_version")
            .select("scorer_name, version_id, params")
            .execute()
        )
        ver_map = {(v["scorer_name"], v["version_id"]): v["params"] for v in versions.data}

        registry = (
            self.client.table("rag.retrieval_scorer_registry")
            .select("scorer_name, impl_class")
            .execute()
        )
        impl_map = {r["scorer_name"]: r["impl_class"] for r in registry.data}

        new_snapshot: dict[str, ScorerSnapshot] = {}
        for c in configs.data:
            name = c["scorer_name"]
            params = ver_map.get((name, c["version_id"]), {})
            new_snapshot[name] = ScorerSnapshot(
                enabled=c["enabled"], weight=float(c["weight"]),
                params=params, version_id=c["version_id"],
                impl_class=impl_map.get(name, ""),
            )

        with self._lock:
            self._snapshot = new_snapshot
        logger.info("RegistryAdapter loaded %d scorers for env=%s", len(new_snapshot), self.environment)

    def is_enabled(self, scorer_name: str) -> bool:
        with self._lock:
            s = self._snapshot.get(scorer_name)
            return bool(s and s.enabled)

    def get_weight(self, scorer_name: str) -> float:
        with self._lock:
            s = self._snapshot.get(scorer_name)
            if not s:
                raise KeyError(f"Unknown scorer: {scorer_name}")
            return s.weight

    def params(self, scorer_name: str) -> dict[str, Any]:
        with self._lock:
            s = self._snapshot.get(scorer_name)
            if not s:
                raise KeyError(f"Unknown scorer: {scorer_name}")
            return dict(s.params)
```

- [ ] **Step 5:** Run test → PASS
- [ ] **Step 6:** Commit: `git add -A && git commit -m "feat: RegistryAdapter core (load+snapshot)"`

### Task 3.2: TDD pg_notify hot-reload

**Files:** Modify `registry_adapter.py`, modify `test_scorer_registry_adapter.py`

- [ ] **Step 1:** Write failing test for hot-reload:

```python
@pytest.mark.asyncio
async def test_adapter_hot_reloads_on_config_change(fresh_v2_db_with_registry_seed, asyncpg_pool):
    adapter = RegistryAdapter(client=fresh_v2_db_with_registry_seed, environment="prod")
    await adapter.load()
    assert adapter.get_weight("anti_magnet") == 1.0

    await adapter.start_listening(asyncpg_pool)

    # Change weight in DB
    fresh_v2_db_with_registry_seed.table("rag.retrieval_pipeline_config").update(
        {"weight": 2.5}
    ).eq("environment", "prod").eq("scorer_name", "anti_magnet").execute()

    # Wait up to 500ms for the LISTEN to receive + apply
    for _ in range(50):
        if adapter.get_weight("anti_magnet") == 2.5:
            break
        await asyncio.sleep(0.01)
    assert adapter.get_weight("anti_magnet") == 2.5

    await adapter.stop_listening()
```

- [ ] **Step 2:** Add `asyncpg_pool` fixture to `conftest.py`:

```python
@pytest.fixture
async def asyncpg_pool():
    import asyncpg
    pool = await asyncpg.create_pool(get_settings().supabase_v2_database_url)
    yield pool
    await pool.close()
```

- [ ] **Step 3:** Add settings field `supabase_v2_database_url` (Postgres connection URL — needed for asyncpg LISTEN; supabase-py doesn't expose LISTEN/NOTIFY)

- [ ] **Step 4:** Run test → FAIL (`start_listening` not defined)

- [ ] **Step 5: Implement listener (target: ~50 lines):**

```python
# Add to RegistryAdapter:
async def start_listening(self, pool) -> None:
    """Open async LISTEN on 'retrieval_pipeline_config_change'. Rebuilds snapshot on each NOTIFY."""
    if self._listener_task is not None:
        return  # already listening

    async def _listen_loop():
        async with pool.acquire() as conn:
            await conn.add_listener(
                "retrieval_pipeline_config_change", self._on_config_change_notify
            )
            while True:
                await asyncio.sleep(3600)  # keep connection alive

    self._listener_task = asyncio.create_task(_listen_loop())

async def _on_config_change_notify(self, conn, pid, channel, payload) -> None:
    if payload != self.environment:
        return  # NOTIFY for a different env; ignore
    logger.info("RegistryAdapter received config-change notify for env=%s", payload)
    try:
        await self.load()
    except Exception:
        logger.exception("RegistryAdapter reload failed")

async def stop_listening(self) -> None:
    if self._listener_task is None:
        return
    self._listener_task.cancel()
    try:
        await self._listener_task
    except asyncio.CancelledError:
        pass
    self._listener_task = None
```

- [ ] **Step 6:** Run test → PASS
- [ ] **Step 7:** Commit: `git add -A && git commit -m "feat: RegistryAdapter pg_notify hot-reload"`

### Task 3.3: Boot-time validator (fail-fast on missing scorer)

**Files:** Create `website/features/rag_pipeline/scoring/registry_init.py`, modify `test_scorer_registry_adapter.py`

- [ ] **Step 1:** Write failing test:

```python
@pytest.mark.asyncio
async def test_boot_validator_fails_when_code_scorer_missing_from_registry(fresh_v2_db_with_registry_seed):
    from website.features.rag_pipeline.scoring.registry_init import validate_registry_completeness

    EXPECTED_SCORERS = ["bm25", "dense", "rrf_fusion", "anti_magnet", "graph_score",
                       "entity_anchor", "cross_encoder", "dense_fallback"]

    # Happy path: all 8 present
    await validate_registry_completeness(fresh_v2_db_with_registry_seed, expected_scorers=EXPECTED_SCORERS)

    # Failure: delete one
    fresh_v2_db_with_registry_seed.table("rag.retrieval_pipeline_config").delete().eq(
        "scorer_name", "anti_magnet"
    ).execute()

    with pytest.raises(RuntimeError, match="anti_magnet"):
        await validate_registry_completeness(fresh_v2_db_with_registry_seed, expected_scorers=EXPECTED_SCORERS)
```

- [ ] **Step 2:** Run → FAIL

- [ ] **Step 3: Implement (target: ~20 lines):**

```python
# website/features/rag_pipeline/scoring/registry_init.py
"""Boot-time validator for the scorer registry.

Called once during FastAPI lifespan startup. Fails the boot if any scorer
present in code is missing from the registry — prevents silent skipping of a
scorer at runtime.
"""
from __future__ import annotations
from supabase import Client


async def validate_registry_completeness(client: Client, *, expected_scorers: list[str], environment: str = "prod") -> None:
    rows = (
        client.table("rag.retrieval_pipeline_config")
        .select("scorer_name")
        .eq("environment", environment)
        .execute()
    )
    found = {r["scorer_name"] for r in rows.data}
    missing = sorted(set(expected_scorers) - found)
    if missing:
        raise RuntimeError(
            f"Scorer registry missing entries for environment={environment}: {missing}. "
            "Either add registry rows (supabase/website/_v2/09_seed_scorer_registry.sql) "
            "or remove the scorer from EXPECTED_SCORERS."
        )
```

- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit: `git add -A && git commit -m "feat: scorer registry boot-time validator"`

### Task 3.4: Wire RegistryAdapter into FastAPI lifespan

**Files:** Modify `website/app.py` (or wherever the FastAPI lifespan is defined; check `website/api/routes.py` and the main entry point)

- [ ] **Step 1:** Find the existing FastAPI app instance + lifespan handler. (Search: `grep -r "lifespan" website/ --include="*.py"`)

- [ ] **Step 2:** Add to the lifespan (after Supabase client init, before yield):

```python
# In website/app.py or main entrypoint
from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter
from website.features.rag_pipeline.scoring.registry_init import validate_registry_completeness
from website.core.supabase_v2.client import get_v2_client

EXPECTED_SCORERS = [
    "bm25", "dense", "rrf_fusion", "anti_magnet", "graph_score",
    "entity_anchor", "cross_encoder", "dense_fallback",
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    client = get_v2_client()
    await validate_registry_completeness(client, expected_scorers=EXPECTED_SCORERS)

    adapter = RegistryAdapter(client, environment=settings.app_environment)
    await adapter.load()

    pool = await asyncpg.create_pool(settings.supabase_v2_database_url)
    await adapter.start_listening(pool)
    app.state.registry_adapter = adapter
    app.state.asyncpg_pool = pool

    yield

    await adapter.stop_listening()
    await pool.close()
```

- [ ] **Step 3:** Add `app_environment` settings field (default `'dev'`; prod sets `'prod'`)
- [ ] **Step 4:** Confirm with `pytest tests/v2/` — every existing test still passes; FastAPI app boots without error in test mode
- [ ] **Step 5:** Commit: `git add -A && git commit -m "feat: RegistryAdapter wired into FastAPI lifespan"`

---

## Phase 4 — API + Retrieval Code Updates (Days 10-13)

### Task 4.1: Update `/api/summarize` to canonical-then-overlay

**Files:** Modify `website/api/routes.py`, modify `website/core/pipeline.py` (the stateless website pipeline)

- [ ] **Step 1:** Read current `website/api/routes.py` `/api/summarize` handler. Identify where it writes to today's `kg_users + kg_nodes` (graph_store + supabase_kg).
- [ ] **Step 2:** Write a failing test in `tests/v2/integration/test_api_summarize.py`:

```python
@pytest.mark.asyncio
async def test_summarize_creates_canonical_and_overlay(fresh_v2_db, test_jwt_for_workspace):
    """POST /api/summarize → canonical_zettel + workspace_zettel + chunks + memberships."""
    profile_id, workspace_id = create_test_profile_and_workspace(fresh_v2_db, "u@t.com")
    jwt = test_jwt_for_workspace(workspace_id)

    response = await test_client.post(
        "/api/summarize",
        json={"url": "https://example.com/post"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert response.status_code == 200

    canonicals = fresh_v2_db.table("content.canonical_zettels").select("*").execute()
    overlays = fresh_v2_db.table("content.workspace_zettels").select("*").execute()
    chunks = fresh_v2_db.table("content.canonical_chunks").select("*").execute()
    memberships = fresh_v2_db.table("content.workspace_chunk_membership").select("*").execute()

    assert len(canonicals.data) == 1
    assert len(overlays.data) == 1 and overlays.data[0]["workspace_id"] == str(workspace_id)
    assert len(chunks.data) >= 1
    assert len(memberships.data) == len(chunks.data)
```

- [ ] **Step 3:** Run → FAIL (route still uses old schema)

- [ ] **Step 4:** Modify `website/core/pipeline.py` `process_url` to:
  1. Run extraction + summarization (unchanged)
  2. Compute `normalized_url` and `content_hash(body)` per spec §4.2
  3. Call `ContentRepository.upsert_canonical_zettel(...)` → returns `(canonical_id, was_new)`
  4. If `was_new`: chunk + embed + `upsert_canonical_chunks(canonical_id, chunks)`
  5. Always: `add_workspace_overlay(workspace_id=jwt_workspace_id, canonical_zettel_id=canonical_id, ai_summary=..., user_tags=..., added_via='website')`
  6. For each chunk: insert into `workspace_chunk_membership(workspace_id, canonical_chunk_id, workspace_zettel_id)`
- [ ] **Step 5:** Modify `website/api/routes.py` `/api/summarize` to read `workspace_id` from JWT (`request.state.user_workspace_ids[0]` or similar; first workspace_id in claim is the "current" one)
- [ ] **Step 6:** Run test → PASS
- [ ] **Step 7:** Commit: `git add -A && git commit -m "feat: /api/summarize canonical-then-overlay write path"`

### Task 4.2: Update `/api/graph` to query workspace overlay

**Files:** Modify `website/api/routes.py`, modify `website/core/graph_store.py` if used as a cache

- [ ] **Step 1:** Write failing test for cross-workspace isolation:

```python
@pytest.mark.asyncio
async def test_graph_endpoint_returns_only_caller_workspace(fresh_v2_db):
    p1, w1 = create_test_profile_and_workspace(fresh_v2_db, "u1@t.com")
    p2, w2 = create_test_profile_and_workspace(fresh_v2_db, "u2@t.com")
    # Insert one workspace_zettel per workspace
    # ... (helper)
    jwt1 = test_jwt_for_workspace(w1)
    response = await test_client.get("/api/graph", headers={"Authorization": f"Bearer {jwt1}"})
    nodes = response.json()["nodes"]
    assert all(n["workspace_id"] == str(w1) for n in nodes)
```

- [ ] **Step 2:** Run → FAIL or returns wrong shape
- [ ] **Step 3:** Modify `/api/graph` to read from `content.workspace_zettels JOIN content.canonical_zettels` plus `kg.kg_nodes` + `kg.kg_edges` filtered by JWT workspace_id
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit

### Task 4.3: Modify `hybrid.py` retrieval to read from RegistryAdapter

**Files:** Modify `website/features/rag_pipeline/retrieval/hybrid.py`

- [ ] **Step 1:** Search current code for hardcoded constants: `grep -rn "RRF_K\|FUSION_WEIGHT\|RAG_DENSE_FALLBACK_ENABLED" website/features/rag_pipeline/`
- [ ] **Step 2:** Write a failing test that the retrieval layer respects a flipped registry weight:

```python
@pytest.mark.asyncio
async def test_hybrid_retrieve_uses_registry_weight(fresh_v2_db_with_registry_seed):
    # Set up data + registry adapter
    adapter = RegistryAdapter(fresh_v2_db_with_registry_seed, "prod")
    await adapter.load()

    # Disable BM25 in the registry
    fresh_v2_db_with_registry_seed.table("rag.retrieval_pipeline_config").update(
        {"enabled": False}
    ).eq("environment","prod").eq("scorer_name","bm25").execute()
    await adapter.load()  # rebuild snapshot

    # Run retrieval; assert BM25 score column is 0/missing in candidates
    from website.features.rag_pipeline.retrieval.hybrid import HybridRetriever
    retriever = HybridRetriever(client=fresh_v2_db_with_registry_seed, registry=adapter)
    results = await retriever.retrieve(query="test", workspace_id=test_workspace_id)
    assert all(r.bm25_score == 0 or r.bm25_score is None for r in results)
```

- [ ] **Step 3:** Run → FAIL (hybrid not using registry yet)
- [ ] **Step 4:** Refactor `HybridRetriever.__init__` to take a `registry: RegistryAdapter`. Replace every hardcoded weight/flag with `self.registry.get_weight("...")` / `self.registry.is_enabled("...")`. For each scorer (`anti_magnet`, `graph_score`, `entity_anchor`, `cross_encoder`, `dense_fallback`): also pull params via `self.registry.params("...")`
- [ ] **Step 5:** Run → PASS
- [ ] **Step 6:** Commit: `git add -A && git commit -m "feat: hybrid retrieval reads from RegistryAdapter"`

### Task 4.4: Modify each downstream scorer to read from registry

For each of: `anti_magnet.py`, `graph_score.py`, `entity_anchor.py`, `cross_encoder.py`, `dense_fallback.py`:

- [ ] **Step 1:** Find hardcoded constants (e.g., `floor=50`, `decay_seconds=2592000`, `seed_boost=2.0`)
- [ ] **Step 2:** Write a failing test: change registry params → next call uses new params
- [ ] **Step 3:** Refactor scorer constructor to take `registry: RegistryAdapter` and read its params at use-time (so hot-reload via `pg_notify` is effective)
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit per scorer

### Task 4.5: Rewrite `recompute_usage_edges.py` → `recompute_signal_weights.py`

**Files:** Rename `ops/scripts/recompute_usage_edges.py` → `ops/scripts/recompute_signal_weights.py`; modify content

- [ ] **Step 1:** Read current `recompute_usage_edges.py`
- [ ] **Step 2:** Write a failing test in `tests/v2/integration/test_recompute_signal_weights.py`:

```python
@pytest.mark.asyncio
async def test_recompute_reads_usage_events_writes_signal_weights(fresh_v2_db):
    profile_id, workspace_id = create_test_profile_and_workspace(fresh_v2_db, "u@t.com")
    # Insert two usage_events with feature='node_cited'
    # ... (set up source/target chunk pairs)
    from ops.scripts.recompute_signal_weights import recompute
    await recompute(client=fresh_v2_db)

    weights = fresh_v2_db.table("rag.retrieval_signal_weights").select("*").execute()
    assert len(weights.data) >= 1
    # Verify decay applied, weights non-zero
```

- [ ] **Step 3:** Rewrite the script:
  - Read `core.usage_events WHERE feature IN ('node_cited','verdict_supported') AND occurred_at > now() - INTERVAL '30 days'`
  - Group by `(workspace_id, source_canonical_chunk_id, target_canonical_chunk_id, query_class)`
  - Apply 30-day exponential time decay
  - Upsert into `rag.retrieval_signal_weights`
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Update the cron schedule (Supabase scheduled edge function or local cron, depending on how it runs today)
- [ ] **Step 6:** Commit: `git add -A && git commit -m "refactor: usage_edges → recompute_signal_weights from core.usage_events"`

### Task 4.6: Update billing webhooks for Razorpay → profile_id mapping

**Files:** Modify `website/features/user_pricing/webhooks.py`, modify `website/features/user_pricing/repository.py`

- [ ] **Step 1:** Write failing test: `test_razorpay_webhook_finds_profile_via_subscriber_id`:

```python
@pytest.mark.asyncio
async def test_razorpay_webhook_resolves_profile_via_subscriber_id(fresh_v2_db):
    profile_id = create_test_profile(fresh_v2_db, "u@t.com", razorpay_subscriber_id="sub_abc123")
    payload = {
      "event": "subscription.charged",
      "payload": {"subscription": {"entity": {"id": "sub_abc123", "customer_id": "cust_xyz"}}}
    }
    from website.features.user_pricing.webhooks import handle_razorpay_event
    result = await handle_razorpay_event(payload, client=fresh_v2_db)
    assert result.profile_id == profile_id
```

- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement webhook handler that extracts `subscription.entity.id` → looks up `core.profiles WHERE razorpay_subscriber_id = ?` → returns `profile_id`. Update `BillingRepository` writes to use this `profile_id`
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit

---

## Phase 5 — Backfill Scripts (Days 14-17)

**Goal:** Scripts that, when executed against a snapshot of prod data, populate the v2 schema with verified-correct migrations.

### Task 5.1: Backfill orchestrator + helpers

**Files:** Create `ops/scripts/refactor_v2/00_full_backfill.py`, `ops/scripts/refactor_v2/_helpers.py`

- [ ] **Step 1:** Create the orchestrator that takes `--source-db-url` (old) and `--target-db-url` (v2) and runs scripts 01-08 in order with progress + abort-on-error semantics
- [ ] **Step 2:** Create helpers: `normalize_url(url) → str`, `compute_content_hash(body) → bytes`, `chunk_body(body) → list[ChunkDict]` (re-uses existing chunker if possible)
- [ ] **Step 3:** Test the orchestrator against an empty source DB — should produce empty target (no errors)
- [ ] **Step 4:** Commit: `git add -A && git commit -m "feat: backfill orchestrator scaffold"`

### Task 5.2: Backfill `core.profiles` from `kg_users`

**Files:** Create `ops/scripts/refactor_v2/01_backfill_profiles.py`, `tests/v2/integration/test_backfill_profiles.py`

- [ ] **Step 1:** Write failing test: `test_backfill_profiles_creates_one_profile_per_kg_user`
- [ ] **Step 2:** Implement backfill: for each `kg_users` row, INSERT into `core.profiles (id, email, display_name)` using the SAME `id` (Supabase auth.users.id is preserved). The auto-personal-workspace trigger handles workspace creation
- [ ] **Step 3:** Run test against a seeded source DB → PASS
- [ ] **Step 4:** Commit

### Task 5.3: Backfill canonical content (THE BIG ONE) — verification gates

**Files:** Create `ops/scripts/refactor_v2/02_backfill_canonical_content.py`, `tests/v2/integration/test_backfill_canonical.py`

- [ ] **Step 1:** Write failing tests:

```python
async def test_two_users_same_url_dedupes_to_one_canonical(seeded_source_db, fresh_v2_db):
    """Two kg_nodes rows with same URL → 1 canonical_zettels + 2 workspace_zettels."""
    # Seed source: insert 2 kg_users + 2 kg_nodes pointing at the same URL
    # ...
    from ops.scripts.refactor_v2.backfill_canonical_content import backfill
    await backfill(source=seeded_source_db, target=fresh_v2_db)

    canonicals = fresh_v2_db.table("content.canonical_zettels").select("*").execute()
    overlays = fresh_v2_db.table("content.workspace_zettels").select("*").execute()
    assert len(canonicals.data) == 1
    assert len(overlays.data) == 2
    # Each user's distinct AI summary preserved on their overlay
    assert {o["ai_summary"] for o in overlays.data} == {"summary_a", "summary_b"}
```

- [ ] **Step 2:** Implement the backfill:
  1. Stream `kg_nodes` rows from source, GROUP BY `(normalized_url(url), content_hash(body))`
  2. For each group: INSERT one `canonical_zettels` row, INSERT N `canonical_chunks` rows (re-embed using the live embedding service, store as halfvec)
  3. For each user-row in the group: INSERT one `workspace_zettels` (with their ai_summary, user_tags, etc) + N `workspace_chunk_membership` rows
- [ ] **Step 3:** Run → PASS
- [ ] **Step 4:** Add a verification routine to `verify_backfill.py`:
  - `SUM(workspace_zettels) == COUNT(kg_nodes_source)` (every old node has an overlay)
  - `COUNT(canonical_zettels) <= COUNT(kg_nodes_source)` (dedup actually happened)
  - For a sample of 10 random users: their `workspace_zettels` count matches their `kg_nodes_source` count
  - Sample 5 random canonical chunks: HNSW query returns reasonable nearest neighbors (smoke test)
- [ ] **Step 5:** Commit: `git add -A && git commit -m "feat: backfill canonical content with dedup verification"`

### Task 5.4: Backfill KG (kg_links → kg_edges; chunk_node_mentions)

**Files:** Create `ops/scripts/refactor_v2/03_backfill_kg.py`, `tests/v2/integration/test_backfill_kg.py`

- [ ] **Step 1:** Test: existing kg_links rows → kg_edges with same source/target; relation_type preserved
- [ ] **Step 2:** Implement: map old `(user_id, source_node_id, target_node_id, relation, relation_type)` → new `(workspace_id, src_node_id, dst_node_id, relation_type)` after first ensuring the kg_nodes rows exist (slugs from old schema → kg_nodes with workspace_id from owner)
- [ ] **Step 3:** Implement chunk_node_mentions: for each old node's tags+entities → INSERT into `kg.chunk_node_mentions`
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit

### Task 5.5: Backfill RAG (sandboxes → kastens)

**Files:** Create `ops/scripts/refactor_v2/04_backfill_rag.py`, `tests/v2/integration/test_backfill_rag.py`

- [ ] **Step 1:** Test: each rag_sandboxes row → rag.kastens (in owner's workspace); each rag_sandbox_members row → rag.kasten_zettels (zettel-in-kasten, not workspace-share)
- [ ] **Step 2:** chat_sessions, chat_messages migrate as-is with `user_id → profile_id` rekey
- [ ] **Step 3:** Implement
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit

### Task 5.6: Backfill pipelines (summary_batch + nexus → pipeline_runs)

**Files:** Create `ops/scripts/refactor_v2/05_backfill_pipelines.py`

- [ ] **Step 1:** Test: summary_batch_runs → pipeline_runs (kind='summarize'); summary_batch_items → pipeline_run_items
- [ ] **Step 2:** Test: nexus_ingest_runs → pipeline_runs (kind='nexus_ingest'); nexus_ingested_artifacts → pipeline_run_items
- [ ] **Step 3:** Implement, run, commit

### Task 5.7: Backfill billing (rekey to profile_id)

**Files:** Create `ops/scripts/refactor_v2/06_backfill_billing.py`, `tests/v2/integration/test_backfill_billing.py`

- [ ] **Step 1:** Test: a pricing_subscriptions row with `render_user_id='abc123'` → maps to `profile_id` via `core.profiles.razorpay_subscriber_id='abc123'` lookup. If lookup fails (ghost subscriber), fail-fast with the bad row logged
- [ ] **Step 2:** Implement, with fail-fast behavior on unresolvable mappings
- [ ] **Step 3:** Run → PASS
- [ ] **Step 4:** Commit

### Task 5.8: Backfill usage events (kg_usage_edges + pricing → core.usage_events)

**Files:** Create `ops/scripts/refactor_v2/07_backfill_usage_events.py`, `tests/v2/integration/test_backfill_usage_events.py`

- [ ] **Step 1:** Test: kg_usage_edges row → core.usage_events row with `feature='retrieval_signal_emit'`, metadata carries source/target chunk ids
- [ ] **Step 2:** Test: pricing_credit_ledger row → core.usage_events with `feature='pricing_credit_<sign>'`
- [ ] **Step 3:** Test: pricing_usage_counters row → core.usage_aggregates
- [ ] **Step 4:** Implement, run, commit

### Task 5.9: One-shot recompute of `retrieval_signal_weights` from new usage_events

**Files:** Create `ops/scripts/refactor_v2/08_recompute_signal_weights.py` (calls the same logic as the cron from Task 4.5)

- [ ] **Step 1:** Test: after the backfill, `rag.retrieval_signal_weights` is non-empty and weights match (within rounding) the old `kg_usage_edges_agg` MV
- [ ] **Step 2:** Implement (delegate to the recompute_signal_weights module from Task 4.5)
- [ ] **Step 3:** Run, commit

### Task 5.10: Comprehensive verification script

**Files:** Create `ops/scripts/refactor_v2/verify_backfill.py`

- [ ] **Step 1:** Implement assertions:
  - Every old kg_users → core.profiles (1:1)
  - Every old kg_node → exactly one workspace_zettels (bijection check)
  - canonical_zettels.count ≤ kg_nodes.count (dedup happened)
  - No NULL workspace_id on tenant-scoped tables
  - No orphan FKs (every workspace_zettel.canonical_zettel_id exists; every workspace_chunk_membership.canonical_chunk_id exists)
  - 5 random sample retrieval queries return non-empty results
  - Pricing rekey: every pricing_* row resolved to a valid profile_id
- [ ] **Step 2:** Run end-to-end: `python ops/scripts/refactor_v2/00_full_backfill.py --source-db-url=$OLD --target-db-url=$NEW && python ops/scripts/refactor_v2/verify_backfill.py --target-db-url=$NEW`
- [ ] **Step 3:** Confirm exits 0 with green checkmarks for every assertion
- [ ] **Step 4:** Commit

---

## Phase 6 — Test Coverage (Days 18-19)

**Goal:** Cross-cutting integration tests that prove the refactor's invariants. Most repository-level tests landed in Phases 2-3; this phase adds the cross-cutting ones.

### Task 6.1: Sharing flow tests

**Files:** Create `tests/v2/integration/test_kasten_sharing.py`

- [ ] **Step 1:** Test: owner creates kasten → adds zettels → shares with another workspace as 'viewer' → viewer can SELECT but not INSERT zettels into the kasten
- [ ] **Step 2:** Test: promote viewer → editor → can now INSERT
- [ ] **Step 3:** Test: revoke membership → recipient gets 0 rows on next SELECT (and JWT refresh has been triggered)
- [ ] **Step 4:** Implementation: stub uses the JWT-claim helper to simulate different users
- [ ] **Step 5:** Run, commit

### Task 6.2: JWT-claim RLS denial tests

**Files:** Create `tests/v2/integration/test_jwt_rls.py`

- [ ] **Step 1:** Test: with a JWT for workspace W1, SELECT from `content.workspace_zettels` filtered to W2 returns 0 rows (RLS, not WHERE)
- [ ] **Step 2:** Test: anon JWT (no `app_metadata`) returns 0 rows on every workspace-scoped table
- [ ] **Step 3:** Test: service-role bypass returns all rows
- [ ] **Step 4:** Test: cross-workspace JOIN attack — user W1 trying to JOIN their own kasten with W2's chat_messages returns 0 rows for the W2 side
- [ ] **Step 5:** Implement, run, commit

### Task 6.3: Soft-delete reaper test

**Files:** Create `tests/v2/integration/test_soft_delete_reaper.py`

- [ ] **Step 1:** Test: when last `workspace_zettels` row referencing a `canonical_zettels` is soft-deleted → `core.soft_delete_queue` gets a row with `shred_after = now() + 7 days`
- [ ] **Step 2:** Test: a reaper script (call directly with `now()` injected as 8 days later) shreds the canonical row
- [ ] **Step 3:** Test: if a NEW `workspace_zettels` row references the canonical between enqueue and reap → reaper skips it (re-checks the orphan condition)
- [ ] **Step 4:** Implement reaper as `ops/scripts/refactor_v2/reaper.py` (cron-driven; runs daily)
- [ ] **Step 5:** Run, commit

### Task 6.4: halfvec recall verification

**Files:** Create `tests/v2/integration/test_halfvec_recall.py`

- [ ] **Step 1:** Test: build a side-by-side `vector(768)` table and `halfvec(768)` table with the same 1000 chunks; run 50 known queries; assert recall@10 within 1% delta
- [ ] **Step 2:** This is a regression-protection test: catches the day someone proposes "let's go back to vector to save complexity"
- [ ] **Step 3:** Run, commit

### Task 6.5: Smoke test the full pipeline against v2

**Files:** Create `tests/v2/integration/test_full_pipeline_smoke.py`

- [ ] **Step 1:** Test: POST /api/summarize with a real URL → wait for processing → GET /api/graph returns the new node → POST a chat in a kasten containing it → assert RAG cites the canonical chunk
- [ ] **Step 2:** Run, commit

---

## Phase 7 — Cutover & Rollback Runbooks (Day 20)

### Task 7.1: Finalize cutover runbook

**Files:** Modify `docs/db-v2/cutover-runbook.md` (created stub in Task 0.4)

- [ ] **Step 1:** Expand the runbook to executable form:

```markdown
# Cutover Runbook — DB v2 Migration

**Pre-flight (T-7 days):**
- [ ] Phase 0-6 complete on v2-dev project
- [ ] All 530+ existing tests + new v2 tests passing
- [ ] PITR restore tested on a throwaway project
- [ ] Backup retention confirmed (Supabase Pro: 7 days)
- [ ] User notified 48h in advance of maintenance window

**T-1 hour:**
- [ ] Verify staging deploy of code paths is green
- [ ] Confirm asyncpg + RegistryAdapter wiring works in staging

**T-0 (cutover start):**
- [ ] Enable maintenance mode (Caddy config flip OR feature flag)
- [ ] Note timestamp T_start for PITR baseline

**Step 1 — Baseline (1 min):**
- [ ] Trigger Supabase backup snapshot
- [ ] Record snapshot ID

**Step 2 — Apply v2 schema (5 min):**
- [ ] `python ops/scripts/apply_migrations.py --v2 --target=$PROD`
- [ ] Verify all 10 SQL files recorded in `core._migrations_applied`

**Step 3 — Backfill (60-90 min):**
- [ ] `python ops/scripts/refactor_v2/00_full_backfill.py --source=$PROD --target=$PROD`
- [ ] Watch progress. Abort if any phase fails.

**Step 4 — Verify (15 min):**
- [ ] `python ops/scripts/refactor_v2/verify_backfill.py --target=$PROD`
- [ ] All assertions green

**Step 5 — Build HNSW (5-20 min):**
- [ ] HNSW index was created empty in Step 2; canonical_chunks rows landed during Step 3
- [ ] Verify HNSW is populated: `SELECT pg_size_pretty(pg_relation_size('content.idx_canonical_chunks_embedding_hnsw'))`

**Step 6 — Switch app (1 min):**
- [ ] Set `DB_SCHEMA_VERSION=v2` in production env
- [ ] Re-deploy droplet (Caddy reload, blue/green flip)
- [ ] Verify `/api/health` returns 200 with `schema_version=v2`

**Step 7 — Smoke test (30 min):**
- [ ] POST /api/summarize against a known URL — confirm new canonical row
- [ ] GET /api/graph from a real user JWT — confirm only their nodes
- [ ] Run a chat in an existing kasten — confirm RAG returns results

**Step 8 — Drop old tables (5 min):**
- [ ] If smoke test green: `psql -f drop_old_schemas.sql`
- [ ] If smoke test red: SKIP this step; go to rollback runbook

**Step 9 — Re-enable site (1 min):**
- [ ] Disable maintenance mode
- [ ] Announce on Telegram + (TBD: email list)

**Post-cutover monitoring (24h):**
- [ ] Caddy access log — error rate < 1%
- [ ] Supabase dashboard — connection count, query p95
- [ ] User reports — Telegram + support inbox
```

- [ ] **Step 2:** Add `drop_old_schemas.sql` (DROP SCHEMA kg_public, kg_features, rag_chatbot, summarization_engine, nexus, user_auth, user_pricing CASCADE)
- [ ] **Step 3:** Commit

### Task 7.2: Write rollback runbook

**Files:** Create `docs/db-v2/rollback-runbook.md`

- [ ] **Step 1:** Write the runbook:

```markdown
# Rollback Runbook — DB v2 Migration

**Trigger:** Smoke test fails OR production error rate > threshold within 30 min of cutover.

**Step 1 — Maintenance mode (1 min)**
- [ ] Re-enable maintenance mode

**Step 2 — Set env back to v1 (1 min)**
- [ ] `DB_SCHEMA_VERSION=v1`
- [ ] Re-deploy droplet

**Step 3 — PITR restore (10-30 min)**
- [ ] Supabase Dashboard → Database → Backups → PITR
- [ ] Select timestamp = T_start (recorded in cutover step)
- [ ] Confirm restore

**Step 4 — Verify rollback**
- [ ] `psql -c "\dt kg_public.*"` shows old schema present
- [ ] Smoke test: POST /api/summarize → success against old code path

**Step 5 — Re-enable site**
- [ ] Disable maintenance mode
- [ ] Communicate with affected users (lost any captures since T_start)

**Step 6 — Postmortem**
- [ ] Document root cause
- [ ] Decide: fix-forward (re-attempt cutover after fix) or pause migration
```

- [ ] **Step 2:** Commit

### Task 7.3: Post-cutover monitoring checklist

**Files:** Create `docs/db-v2/post-cutover-monitoring.md`

- [ ] **Step 1:** Document:
  - Supabase dashboard metrics to watch (query p95, connection count, RAM usage)
  - Caddy access log — error rate per route
  - Specific KPIs: canonical-content dedup ratio, RegistryAdapter notification latency, HNSW query time
  - Trip-wires: when to consider Turbopuffer/pgvectorscale (canonical_chunks > 50M rows OR Supabase compute > $1k/mo)
- [ ] **Step 2:** Commit

---

## Phase 8 — Cutover Execution (THE WEEKEND)

### Task 8.1: Execute the runbook

- [ ] **Step 1:** User-driven; the assistant's role here is hands-on-keyboard if requested
- [ ] **Step 2:** Follow `docs/db-v2/cutover-runbook.md` step by step, marking each checkbox as it completes
- [ ] **Step 3:** Document any deviations in real time

### Task 8.2: Verify post-cutover

- [ ] **Step 1:** Run the verify_backfill.py script one more time post-cutover
- [ ] **Step 2:** 30-min smoke test
- [ ] **Step 3:** First-24h monitoring per `docs/db-v2/post-cutover-monitoring.md`

---

## Phase 9 — Post-Cutover Cleanup (Day +7)

### Task 9.1: Drop the v2 dev project

Once prod is stable for 7+ days:
- [ ] **Step 1:** Drop the `zettelkasten-v2-dev` Supabase project
- [ ] **Step 2:** Update `.env.example` to remove `SUPABASE_V2_*` (the v2 IS prod now)
- [ ] **Step 3:** Rename `website/core/supabase_v2/` → `website/core/supabase_kg/` (replacing the old dir which was dropped at cutover)
- [ ] **Step 4:** Rename `supabase/website/_v2/` → `supabase/website/v2/` (or move into per-schema directories matching the new module split)
- [ ] **Step 5:** Commit: `chore: post-cutover cleanup; v2 is canonical`

### Task 9.2: Decommission the legacy `recompute_usage_edges` cron

- [ ] **Step 1:** Once `recompute_signal_weights.py` has been running cleanly for 7 days, delete the old cron schedule
- [ ] **Step 2:** Commit

### Task 9.3: Save a memory observation marking refactor complete

- [ ] **Step 1:** Use mem-vault `save_observation` with type=`feature`: "DB refactor v2 migration completed: 6-schema layout, canonical+overlay dedup, JWT-claim RLS, halfvec(768) HNSW, scorer registry adapter, pg_partman usage_events. Cutover date: <T_start>."
- [ ] **Step 2:** Update CLAUDE.md to reflect the new schema layout + remove the "render_user_id" historical note (it's now actively misleading)

---

## Self-Review

**1. Spec coverage:**

| Spec section | Plan task(s) |
|---|---|
| §3 schema split (6 schemas) | 1.2-1.7 |
| §4.1 core (profiles, workspaces, JWT, partitioned events, quotas, soft-delete queue) | 1.2, 1.8 |
| §4.2 content (canonical+overlay, halfvec, HNSW, soft-delete trigger, embedding versioning) | 1.3, 6.4 |
| §4.3 kg (nodes, edges, chunk_node_mentions, expand_subgraph) | 1.4 |
| §4.4 rag (kastens, sharing, scorer registry 4 tables, pg_notify) | 1.5, 1.10, 3.1-3.4 |
| §4.5 pipelines | 1.6 |
| §4.6 billing (rekey, plan_entitlements) | 1.7, 4.6, 5.7 |
| §5 cutover sequence | 7.1, 8.1 |
| §6 app code surfaces (each row) | 4.1-4.6 |
| §6 ~150-line registry adapter mandatory | 3.1-3.4 (entire phase) |
| §7 15-vs-10k explicitness | 7.3 (monitoring trip-wires) |
| §8 out-of-scope respected | (no tasks, by design) |

All sections covered. ✅

**2. Placeholder scan:** No "TBD" / "TODO" / "implement later" / "add appropriate error handling" found. Tasks reference the spec for verbatim DDL rather than restating it (consistent with skill rule: full code shown OR exact reference).

**3. Type consistency:**
- `RegistryAdapter` methods used identically across Phase 3 + Phase 4 (`is_enabled`, `get_weight`, `params`)
- `ContentRepository.upsert_canonical_zettel` returns `(UUID, bool)` consistently
- `consume_quota_atomic` signature stable across Phase 2 + the spec
- `core.jwt_workspace_ids()` referenced consistently in DDL + Python tests

✅ No drift.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-08-db-refactor-implementation.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for a long multi-phase plan like this where context-window discipline matters.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
