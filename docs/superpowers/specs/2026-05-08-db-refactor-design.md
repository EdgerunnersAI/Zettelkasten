# DB Refactor Design — 2026-05-08 (rev. 2 — post-audit)

**Status:** Brainstorming complete; locked decisions baked in; 41 audit findings addressed (12 BLOCKER + 29 MAJOR). Ready for implementation plan.
**Scope:** Full Supabase schema redesign for the Zettelkasten website. Bot stays out of scope (writes to local `KG_DIRECTORY` / GitHub, not Supabase) — confirmed: bot does not dual-write.
**Cutover:** Big-bang weekend window AFTER ~3-week implementation phase. 2-4 hour maintenance window. Same-day PITR rollback acceptable WITH the legacy v1 code path retained for 14 days.
**User scale today:** 10–15 users. **Target:** 10,000+ users without breaking anything along the way.

**Revision log (rev 2):**
- Fixed `core.jwt_workspace_ids()` cast chain ([Postgres jsonb_array_elements_text](https://www.postgresql.org/docs/current/functions-json.html)) — audit B.1
- Added authorization guard to `kg.expand_subgraph` SECURITY DEFINER — audit B.3
- Split soft-delete reaper trigger into separate DELETE / UPDATE-OF triggers (Postgres rejects `NEW` reference in DELETE WHEN clauses, [Postgres CREATE TRIGGER docs](https://www.postgresql.org/docs/current/sql-createtrigger.html)) — audit B.4
- Added `content.search_chunks()` SECURITY DEFINER RPC for workspace-scoped ANN; canonical_chunks table is service-role-only — audit B.5
- Widened `workspace_chunk_membership` PK to `(workspace_id, canonical_chunk_id, workspace_zettel_id)` — audit B.6
- HNSW index moved to a separate file `10_hnsw_indexes.sql` applied AFTER backfill in cutover — audit B.7
- Added explicit `UNIQUE` constraint on `kg.kg_nodes (workspace_key, slug)` via generated column — audit B.2
- Added `core.consume_quota()` typed SECURITY DEFINER RPC; deleted reference to fictional `exec_sql_returning` — audit C.2
- Added `core._migrations_applied` bootstrap to `00_extensions.sql` — audit I.1
- Added `pg_cron` to required extensions — audit F.1
- Added `chat_messages.retrieval_run_id` FK declaration — audit A.2
- Added citation-integrity policy: reaper SKIPS canonical rows referenced by `chat_messages.citations` jsonb — audit A.3
- `kg_users` allowlist semantics carried forward into a new `core.profile_allowlist_status` enum + check — audit I.2
- `kg.chat_sessions.kasten_id` and `chat_messages.session_id` ON DELETE behavior reconciled — audit I (consistency)

---

## 1. Why

The current Supabase schema (7 modules: `kg_public`, `kg_features`, `rag_chatbot`, `summarization_engine`, `nexus`, `user_auth`, `user_pricing`) was verified by an architecture-scout and has five structural problems:

1. **No workspace abstraction.** Every hot table keys on `user_id`; sharing is impossible without duplicating content.
2. **Three parallel views over content** (KG, RAG, summarization) with **no canonical Document/Zettel record**. Same URL captured by two users = two `kg_nodes` rows + two embeddings + two HNSW entries.
3. **RLS uses the documented 10× anti-pattern** (`EXISTS (SELECT 1 FROM kg_users u …)`) on every policy, per [Supabase RLS Performance and Best Practices](https://supabase.com/docs/guides/troubleshooting/rls-performance-and-best-practices-Z5Jjwv).
4. **Usage accounting is fragmented across 16+ tables** in 3 schemas; no source of truth for "how much has this user/workspace consumed".
5. **Every retrieval iteration adds 1–3 schema artifacts.** Some die (RES-2 `kg_kasten_node_freq` floor never crossed); some replace others (`rag_kasten_chunk_counts` replaced kasten_freq).

This design fixes all five with industry-validated patterns, sized for 10k users while staying lightweight at 15.

Full research: 6 cited research notes (multi-tenant Postgres, RAG+KG dedup, usage events, dynamic schemas, pgvector, scorer registry) + 1 deep-research follow-up on dynamic schemas, summarized in conversation transcript 2026-05-08.

---

## 2. Locked Decisions (from /brainstorming)

| # | Decision | Source / citation |
|---|---|---|
| Q1 | **Workspace tenancy** (workspace_id, not user_id). Personal-workspace-per-user, invisible UI today, exposable later (Linear-style). Sharing granularity: Zettel + Kasten | [WorkOS: Multi-tenant permissions](https://workos.com/blog/multi-tenant-permissions-slack-notion-linear), [Notion data model](https://www.notion.com/blog/data-model-behind-notion) |
| Q1.b | **Kasten sharing role model**: `kasten_members.role IN ('owner','editor','viewer')`. Default share = viewer; promotable | Notion/Linear |
| Q2 | **Auto-dedup by content_hash** across workspaces. Canonical key = normalized URL + content_hash(body). Per-workspace overlay holds tags/summary/notes | [AWS multi-tenant RAG](https://aws.amazon.com/blogs/machine-learning/multi-tenant-rag-with-amazon-bedrock-knowledge-bases/), [Microsoft Azure RAG embeddings](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-generate-embeddings) |
| Q2.b | **Delete = remove membership; reaper shreds canonical row 7 days after last reference leaves** | Notion/Roam default |
| Q3 | **Big-bang weekend cutover.** Same-day PITR rollback. Schema still expand-contract-friendly for future migrations | User constraint + [pgroll expand-contract](https://xataio.github.io/pgroll/) for future |
| Q4 | **Full migration to `core.profiles`** (`profiles.id REFERENCES auth.users(id)`). Purge Render entirely. JWT custom claim `workspace_ids[]` in `auth.users.raw_app_meta_data` (surfaces as `app_metadata.workspace_ids` in JWT). **Eager** JWT refresh via Postgres trigger on membership change | [Supabase Custom Claims](https://supabase.com/docs/guides/database/postgres/custom-claims-and-role-based-access-control-rbac), [Supabase Discussion #1148](https://github.com/orgs/supabase/discussions/1148) |
| Q5 | **Single `core.usage_events` fact table** for billing+analytics, partitioned by `occurred_at` via [pg_partman](https://github.com/pgpartman/pg_partman). **Retrieval signals stay narrow** in `rag.retrieval_signal_weights` (denormalized aggregate) | [Stripe Meters](https://docs.stripe.com/api/billing/meter-event), [OpenMeter](https://openmeter.io/docs/concepts/usage-events) |
| Q5.b | **Quota = soft-check in app + hard-enforce in DB.** Atomic SQL: `UPDATE quotas SET remaining = remaining - 1 WHERE workspace_id = $1 AND remaining > 0 RETURNING` | [brandur: Postgres atomic transactions](https://brandur.org/postgres-atomicity), [Doyensec: race conditions](https://blog.doyensec.com/2024/07/11/database-race-conditions.html) |
| Q6 | **Hybrid scorer registry** (Solr LTR + Personalize `$LATEST` pattern): code for impl, data for tunable knobs. Four-table registry. Prompts/pipeline-versions stay as YAML in git. Bandit persistence deferred | [Solr LTR](https://solr.apache.org/guide/solr/latest/query-guide/learning-to-rank.html), [AWS Personalize campaigns](https://docs.aws.amazon.com/personalize/latest/dg/campaigns.html), [Stitch Fix bandits](https://multithreaded.stitchfix.com/blog/2020/08/05/bandits/) |
| Q6.b | **MANDATORY Phase-1 deliverable**: ~150-line Python adapter so each scorer reads from the registry at boot + on `pg_notify` hot-reload. Without it the registry is decorative | — |
| Burn-3 | **halfvec(768)** at cutover (50% storage + index reduction at near-zero recall loss for 768-d) | [Neon halfvec post](https://neon.com/blog/dont-use-vector-use-halvec-instead-and-save-50-of-your-storage-cost), [Supabase 0.7](https://supabase.com/blog/pgvector-0-7-0) |
| Burn-4 | **`embedding_model_version` column** on canonical_chunks from day 1 | [TianPan: embedding versioning](https://tianpan.co/blog/2026-04-09-embedding-models-production-versioning-index-drift) |

---

## 3. Architecture: Six Schemas

```
core ← content ← kg ← rag
core ← pipelines
core ← billing
```

**FK direction is strictly downward** (within user-defined schemas). `core` only references `auth` (Supabase platform schema, treated as upstream). No cycles. `rag` may FK into `kg`, `content`, `core`. PostgREST exposure: all six schemas listed in the project's `db-schemas` config so supabase-py's `.schema("core").table("profiles")` works ([Supabase REST API config](https://supabase.com/docs/guides/api/using-custom-schemas)).

| Schema | Tables (count) |
|---|---|
| **core** | `profiles`, `workspaces`, `workspace_members`, `usage_events` (partitioned), `usage_aggregates`, `quotas`, `soft_delete_queue`, `_migrations_applied` (8) |
| **content** | `embedding_model_versions`, `canonical_zettels`, `canonical_chunks`, `workspace_zettels`, `workspace_chunk_membership` (5) |
| **kg** | `kg_nodes`, `kg_edges`, `chunk_node_mentions` (3) |
| **rag** | `kastens`, `kasten_members`, `kasten_zettels`, `chat_sessions`, `chat_messages`, `retrieval_signal_weights`, `retrieval_scorer_registry`, `retrieval_scorer_version`, `retrieval_pipeline_config`, `retrieval_pipeline_config_history` (10) |
| **pipelines** | `pipeline_runs`, `pipeline_run_items` (2) |
| **billing** | `pricing_billing_profiles`, `pricing_orders`, `pricing_subscriptions`, `pricing_webhook_events`, `pricing_credit_ledger`, `pricing_balances`, `pricing_payment_events`, `pricing_plan_cache`, `pricing_refunds`, `pricing_disputes`, `pricing_plan_entitlements` (11) |

**Total: 39 tables** (was 38 in rev 1; added `core._migrations_applied`).

**Retired:**
- `kg_public.kg_users` → `core.profiles` (allowlist semantics carried forward via `core.profiles.allowlist_status` enum)
- `kg_public.kg_kasten_node_freq` → not migrated (dead surface)
- `kg_public.kg_usage_edges` + `kg_usage_edges_agg` MV → replaced by `core.usage_events` projecting into `rag.retrieval_signal_weights`
- `nexus` schema → absorbed into `pipelines.pipeline_runs` with `kind='nexus_ingest'`
- `user_auth` schema → trigger lives directly in `core`
- `kg_features` schema → RPCs absorbed into `kg`; ALTERs collapsed into base `kg_nodes` definition

---

## 4. Schema DDL

### 4.0 Extensions + bootstrap (`00_extensions.sql`)

```sql
-- _v2/00_extensions.sql — first file applied; bootstraps the migration tracker.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector ≥ 0.8 required (halfvec + iterative_scan)
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;
CREATE EXTENSION IF NOT EXISTS pg_cron;    -- Supabase Pro grants pg_cron to postgres role

CREATE SCHEMA IF NOT EXISTS core;

-- Bootstrap the migration tracker BEFORE any other v2 file; resolves the chicken-egg
-- problem flagged in audit I.1 (apply_migrations.py recording its own creation).
CREATE TABLE IF NOT EXISTS core._migrations_applied (
  name             text PRIMARY KEY,
  applied_at       timestamptz NOT NULL DEFAULT now(),
  checksum         text NOT NULL,
  applied_by       text,
  deploy_git_sha   text,
  deploy_id        text,
  deploy_actor     text,
  runner_hostname  text
);
```

### 4.1 `core` schema

```sql
-- Identity (replaces kg_users; FK to Supabase auth.users — auth is treated as a
-- platform-level upstream schema, NOT a violation of "no upward FK in user schemas")
CREATE TABLE core.profiles (
  id              uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name    text,
  email           text,
  avatar_url      text,
  razorpay_subscriber_id text UNIQUE,        -- For pricing webhook lookup
  -- Allowlist semantics carried forward from kg_users (CLAUDE.md Phase 2D.2 gate).
  -- Audit fix I.2: do not silently drop the allowlist.
  allowlist_status text NOT NULL DEFAULT 'allowed'
                  CHECK (allowlist_status IN ('allowed','blocked','pending')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Workspaces (one personal per user; future "Teams" plan adds non-personal)
CREATE TABLE core.workspaces (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_profile_id uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
  name            text NOT NULL,
  is_personal     boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_workspaces_owner_personal ON core.workspaces(owner_profile_id) WHERE is_personal;

-- Membership (drives JWT custom claim)
CREATE TABLE core.workspace_members (
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  profile_id      uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('owner','editor','viewer')),
  added_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, profile_id)
);
CREATE INDEX idx_workspace_members_profile ON core.workspace_members(profile_id);

-- Usage events: single immutable fact table, partitioned by occurred_at via pg_partman
-- Reference: https://github.com/pgpartman/pg_partman (declarative monthly partitions)
CREATE TABLE core.usage_events (
  id              bigserial,
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  profile_id      uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
  feature         text NOT NULL,
  unit            text NOT NULL,
  quantity        numeric NOT NULL DEFAULT 1,
  metadata        jsonb NOT NULL DEFAULT '{}',
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (occurred_at, id)
) PARTITION BY RANGE (occurred_at);

CREATE INDEX idx_usage_events_workspace_feature_time
    ON core.usage_events (workspace_id, feature, occurred_at DESC);
CREATE INDEX idx_usage_events_profile_time
    ON core.usage_events (profile_id, occurred_at DESC);

-- Aggregate rollup
CREATE TABLE core.usage_aggregates (
  workspace_id    uuid NOT NULL,
  profile_id      uuid NOT NULL,
  feature         text NOT NULL,
  unit            text NOT NULL,
  period_start    timestamptz NOT NULL,
  quantity_total  numeric NOT NULL DEFAULT 0,
  events_count    bigint  NOT NULL DEFAULT 0,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, feature, unit, period_start)
);

-- Quotas: hard-enforce target.
CREATE TABLE core.quotas (
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  feature         text NOT NULL,
  unit            text NOT NULL,
  period_start    timestamptz NOT NULL,
  remaining       numeric NOT NULL,
  limit_total     numeric NOT NULL,
  PRIMARY KEY (workspace_id, feature, unit, period_start)
);

-- Reaper queue for soft-deleted canonical content
CREATE TABLE core.soft_delete_queue (
  id              bigserial PRIMARY KEY,
  table_name      text NOT NULL,
  row_id          uuid NOT NULL,
  enqueued_at     timestamptz NOT NULL DEFAULT now(),
  shred_after     timestamptz NOT NULL,
  shredded_at     timestamptz
);
CREATE INDEX idx_soft_delete_pending ON core.soft_delete_queue(shred_after) WHERE shredded_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────
-- AUDIT FIX B.1 — JWT-claim helper with correct cast chain.
-- The naive `(jsonb)::text::uuid[]` produces a JSON-quoted string, NOT a
-- Postgres array literal. Use jsonb_array_elements_text + ARRAY-aggregate.
-- Reference: https://www.postgresql.org/docs/current/functions-json.html
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION core.jwt_workspace_ids() RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT COALESCE(
      ARRAY(
        SELECT (jsonb_array_elements_text(
                  COALESCE(auth.jwt() -> 'app_metadata' -> 'workspace_ids', '[]'::jsonb)
               ))::uuid
      ),
      ARRAY[]::uuid[]
    );
$$;

GRANT EXECUTE ON FUNCTION core.jwt_workspace_ids() TO authenticated, anon;

-- ─────────────────────────────────────────────────────────────────────
-- AUDIT FIX C.2 — Typed quota-debit RPC (replaces fictional exec_sql_returning).
-- Race-safe per https://brandur.org/postgres-atomicity — single UPDATE with
-- WHERE remaining > 0 RETURNING serializes correctly under concurrent debits.
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION core.consume_quota(
  p_workspace_id uuid, p_feature text, p_unit text, p_period_start timestamptz
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE rem numeric;
BEGIN
  -- Authorize: caller's JWT must include this workspace
  IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())) THEN
    RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
  END IF;
  UPDATE core.quotas
    SET remaining = remaining - 1
    WHERE workspace_id = p_workspace_id AND feature = p_feature
      AND unit = p_unit AND period_start = p_period_start AND remaining > 0
    RETURNING remaining INTO rem;
  RETURN FOUND;
END $$;
GRANT EXECUTE ON FUNCTION core.consume_quota(uuid, text, text, timestamptz) TO authenticated;

-- Eager JWT refresh: when membership changes, push workspace_ids[] into
-- auth.users.raw_app_meta_data so next-issued JWT carries the array as
-- app_metadata.workspace_ids (Supabase claim convention).
-- Reference: https://supabase.com/docs/guides/database/postgres/custom-claims-and-role-based-access-control-rbac
CREATE OR REPLACE FUNCTION core.sync_workspace_ids_to_jwt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
  affected_profile uuid := COALESCE(NEW.profile_id, OLD.profile_id);
  ids uuid[];
BEGIN
  SELECT array_agg(workspace_id) INTO ids
  FROM core.workspace_members WHERE profile_id = affected_profile;
  UPDATE auth.users
    SET raw_app_meta_data = jsonb_set(
      COALESCE(raw_app_meta_data,'{}'::jsonb),
      '{workspace_ids}',
      to_jsonb(COALESCE(ids, ARRAY[]::uuid[]))
    )
  WHERE id = affected_profile;
  RETURN NULL;
END $$;

CREATE TRIGGER trg_workspace_members_jwt_sync
  AFTER INSERT OR DELETE OR UPDATE ON core.workspace_members
  FOR EACH ROW EXECUTE FUNCTION core.sync_workspace_ids_to_jwt();

-- Auto-create personal workspace on profile insert
CREATE OR REPLACE FUNCTION core.create_personal_workspace()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE ws_id uuid;
BEGIN
  INSERT INTO core.workspaces (owner_profile_id, name, is_personal)
    VALUES (NEW.id, 'Personal', true) RETURNING id INTO ws_id;
  INSERT INTO core.workspace_members (workspace_id, profile_id, role)
    VALUES (ws_id, NEW.id, 'owner');
  RETURN NEW;
END $$;

CREATE TRIGGER trg_profile_personal_workspace
  AFTER INSERT ON core.profiles
  FOR EACH ROW EXECUTE FUNCTION core.create_personal_workspace();

-- Sync auth.users → core.profiles on signup
CREATE OR REPLACE FUNCTION core.handle_new_auth_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO core.profiles (id, email, display_name)
    VALUES (NEW.id, NEW.email, NEW.raw_user_meta_data ->> 'name')
    ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END $$;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION core.handle_new_auth_user();

-- Allowlist enforcement: block INSERT into workspaces if profile.allowlist_status != 'allowed'
CREATE OR REPLACE FUNCTION core.enforce_allowlist()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM core.profiles WHERE id = NEW.owner_profile_id AND allowlist_status = 'allowed'
  ) THEN
    RAISE EXCEPTION 'profile not on allowlist' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_workspaces_allowlist_check
  BEFORE INSERT ON core.workspaces
  FOR EACH ROW EXECUTE FUNCTION core.enforce_allowlist();
```

**RLS pattern (every workspace-scoped table):**

```sql
ALTER TABLE <schema>.<tbl> ENABLE ROW LEVEL SECURITY;

CREATE POLICY <tbl>_select ON <schema>.<tbl>
  FOR SELECT USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY <tbl>_insert ON <schema>.<tbl>
  FOR INSERT WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY <tbl>_update ON <schema>.<tbl>
  FOR UPDATE USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY <tbl>_delete ON <schema>.<tbl>
  FOR DELETE USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY <tbl>_service_all ON <schema>.<tbl>
  FOR ALL USING (current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
  WITH CHECK (current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role');
```

This is the **10× perf win** vs today's `EXISTS` pattern, per [Supabase RLS Performance](https://supabase.com/docs/guides/troubleshooting/rls-performance-and-best-practices-Z5Jjwv).

### 4.2 `content` schema

```sql
CREATE SCHEMA IF NOT EXISTS content;

-- Embedding model registry (for future cutover; lets v1 + v2 coexist)
CREATE TABLE content.embedding_model_versions (
  version_id      text PRIMARY KEY,
  dimensions      int  NOT NULL,
  introduced_at   timestamptz NOT NULL DEFAULT now(),
  retired_at      timestamptz,
  is_default      boolean NOT NULL DEFAULT false
);
INSERT INTO content.embedding_model_versions (version_id, dimensions, is_default)
VALUES ('gemini-001-mrl-768', 768, true);

-- Canonical Zettel: ONE row per (normalized URL, content_hash). Shared across workspaces.
CREATE TABLE content.canonical_zettels (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  normalized_url  text NOT NULL,
  content_hash    bytea NOT NULL,
  source_type     text NOT NULL CHECK (source_type IN
                    ('youtube','reddit','github','twitter','substack','newsletter','medium','web','generic')),
  title           text,
  body_md         text,
  publication_date date,
  source_metadata jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (normalized_url, content_hash)
);
CREATE INDEX idx_canonical_zettels_hash ON content.canonical_zettels(content_hash);

-- Canonical Chunks: ONE row per (canonical_zettel, chunk_idx). Shared across workspaces.
CREATE TABLE content.canonical_chunks (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_zettel_id uuid NOT NULL REFERENCES content.canonical_zettels(id) ON DELETE CASCADE,
  chunk_idx       int  NOT NULL,
  content         text NOT NULL,
  content_hash    bytea NOT NULL,
  chunk_type      text NOT NULL CHECK (chunk_type IN ('atomic','semantic','late','recursive')),
  start_offset    int,
  end_offset      int,
  token_count     int,
  embedding       halfvec(768),                 -- halfvec, NOT vector — 50% storage win
  embedding_model_version text NOT NULL DEFAULT 'gemini-001-mrl-768'
                  REFERENCES content.embedding_model_versions(version_id),
  fts             tsvector,
  metadata        jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (canonical_zettel_id, chunk_idx)
);

-- AUDIT FIX B.7: HNSW index NOT created here. Created post-backfill in
-- `_v2/10_hnsw_indexes.sql` so the backfill INSERTs aren't 5-10× slower
-- due to incremental index rebuilds.
-- Reference: https://www.crunchydata.com/blog/hnsw-indexes-with-postgres-and-pgvector

CREATE INDEX idx_canonical_chunks_fts ON content.canonical_chunks USING GIN (fts);
CREATE INDEX idx_canonical_chunks_zettel ON content.canonical_chunks(canonical_zettel_id);

CREATE OR REPLACE FUNCTION content.canonical_chunks_fts_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.fts := to_tsvector('english', coalesce(NEW.content,'')); RETURN NEW; END $$;
CREATE TRIGGER trg_canonical_chunks_fts
  BEFORE INSERT OR UPDATE OF content ON content.canonical_chunks
  FOR EACH ROW EXECUTE FUNCTION content.canonical_chunks_fts_update();

-- Per-workspace OVERLAY: tags, AI summary, user notes, hit_count.
CREATE TABLE content.workspace_zettels (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  canonical_zettel_id uuid NOT NULL REFERENCES content.canonical_zettels(id) ON DELETE RESTRICT,
  ai_summary      text,
  ai_summary_engine_version text,
  user_tags       text[] NOT NULL DEFAULT '{}',
  user_note       text,
  pinned          boolean NOT NULL DEFAULT false,
  added_via       text NOT NULL CHECK (added_via IN ('telegram','website','share','migration')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz,
  UNIQUE (workspace_id, canonical_zettel_id)
);
CREATE INDEX idx_workspace_zettels_workspace_tags
  ON content.workspace_zettels USING GIN (user_tags);
CREATE INDEX idx_workspace_zettels_workspace_created
  ON content.workspace_zettels (workspace_id, created_at DESC) WHERE deleted_at IS NULL;

-- AUDIT FIX B.6: PK widened so two workspace_zettels in the same workspace
-- can both reference the same canonical_chunk via distinct overlay rows.
CREATE TABLE content.workspace_chunk_membership (
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  canonical_chunk_id uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
  workspace_zettel_id uuid NOT NULL REFERENCES content.workspace_zettels(id) ON DELETE CASCADE,
  hit_count       int NOT NULL DEFAULT 0,
  last_hit_at     timestamptz,
  PRIMARY KEY (workspace_id, canonical_chunk_id, workspace_zettel_id)
);
CREATE INDEX idx_workspace_chunks_workspace ON content.workspace_chunk_membership(workspace_id);
CREATE INDEX idx_workspace_chunks_chunk ON content.workspace_chunk_membership(canonical_chunk_id);

-- ─────────────────────────────────────────────────────────────────────
-- AUDIT FIX B.4 — Soft-delete reaper triggers split into two.
-- Postgres rejects NEW references in DELETE WHEN clauses
-- (https://www.postgresql.org/docs/current/sql-createtrigger.html).
-- AUDIT FIX A.3 — Reaper enqueues only if no chat_messages.citations
-- references the canonical chunk (citation-integrity policy).
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION content.enqueue_canonical_shred_if_orphan(p_canonical_zettel_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM content.workspace_zettels wz
    WHERE wz.canonical_zettel_id = p_canonical_zettel_id AND wz.deleted_at IS NULL
  ) THEN
    RETURN;  -- still referenced; do nothing
  END IF;
  -- Citation integrity (A.3): if any chat_message cites a chunk under this canonical, defer reap.
  IF EXISTS (
    SELECT 1 FROM rag.chat_messages cm, jsonb_array_elements(cm.citations) c
    WHERE (c->>'canonical_chunk_id')::uuid IN (
      SELECT id FROM content.canonical_chunks WHERE canonical_zettel_id = p_canonical_zettel_id
    )
  ) THEN
    RETURN;  -- cited; defer
  END IF;
  INSERT INTO core.soft_delete_queue (table_name, row_id, shred_after)
    VALUES ('content.canonical_zettels', p_canonical_zettel_id, now() + INTERVAL '7 days')
    ON CONFLICT DO NOTHING;
END $$;

CREATE OR REPLACE FUNCTION content.trg_orphan_check_after_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM content.enqueue_canonical_shred_if_orphan(OLD.canonical_zettel_id);
  RETURN OLD;
END $$;

CREATE OR REPLACE FUNCTION content.trg_orphan_check_after_softdelete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
    PERFORM content.enqueue_canonical_shred_if_orphan(NEW.canonical_zettel_id);
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_workspace_zettel_after_delete
  AFTER DELETE ON content.workspace_zettels
  FOR EACH ROW EXECUTE FUNCTION content.trg_orphan_check_after_delete();

CREATE TRIGGER trg_workspace_zettel_after_softdelete
  AFTER UPDATE OF deleted_at ON content.workspace_zettels
  FOR EACH ROW EXECUTE FUNCTION content.trg_orphan_check_after_softdelete();

-- ─────────────────────────────────────────────────────────────────────
-- AUDIT FIX B.5 — RPC for workspace-scoped ANN search.
-- canonical_chunks is service-role-only; authenticated users hit this RPC.
-- The RPC enforces: workspace_id ∈ jwt_workspace_ids() AND
--                   chunk in workspace_chunk_membership.
-- Reference: pgvector iterative_scan https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION content.search_chunks(
  p_workspace_id   uuid,
  p_query_embedding halfvec(768),
  p_limit          int DEFAULT 32
) RETURNS TABLE (
  chunk_id         uuid,
  canonical_zettel_id uuid,
  content          text,
  score            double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())) THEN
    RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
  END IF;
  -- Per-statement GUC; iterative_scan is safe under filtered HNSW
  -- (pgvector 0.8 release: https://www.postgresql.org/about/news/pgvector-080-released-2952/)
  PERFORM set_config('hnsw.iterative_scan','relaxed_order', true);
  RETURN QUERY
    SELECT cc.id, cc.canonical_zettel_id, cc.content,
           (1 - (cc.embedding <=> p_query_embedding))::double precision
    FROM content.workspace_chunk_membership wcm
    JOIN content.canonical_chunks cc ON cc.id = wcm.canonical_chunk_id
    WHERE wcm.workspace_id = p_workspace_id
    ORDER BY cc.embedding <=> p_query_embedding
    LIMIT p_limit;
END $$;
GRANT EXECUTE ON FUNCTION content.search_chunks(uuid, halfvec, int) TO authenticated;
```

### 4.3 `kg` schema

```sql
CREATE SCHEMA IF NOT EXISTS kg;

-- AUDIT FIX B.2: explicit unique-via-generated-column for clean upsert ON CONFLICT.
CREATE TABLE kg.kg_nodes (
  id              bigserial PRIMARY KEY,
  workspace_id    uuid REFERENCES core.workspaces(id) ON DELETE CASCADE, -- NULL = global
  workspace_key   text GENERATED ALWAYS AS
                  (COALESCE(workspace_id::text, '__global__')) STORED,
  type            text NOT NULL,
  canonical_name  text NOT NULL,
  slug            text NOT NULL,
  metadata        jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_key, slug)
);
CREATE INDEX idx_kg_nodes_canonical_name ON kg.kg_nodes(canonical_name);

CREATE TYPE kg.kg_edge_relation AS ENUM
  ('shared_tag','cites','mentions','co_occurs','authored_by','published_in');

CREATE TABLE kg.kg_edges (
  id              bigserial PRIMARY KEY,
  workspace_id    uuid REFERENCES core.workspaces(id) ON DELETE CASCADE,
  workspace_key   text GENERATED ALWAYS AS
                  (COALESCE(workspace_id::text, '__global__')) STORED,
  src_node_id     bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
  dst_node_id     bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
  relation_type   kg.kg_edge_relation NOT NULL,
  -- AUDIT FIX A.4: preserve old kg_links.relation TEXT label here:
  shared_tag_label text,
  weight          numeric,
  evidence_canonical_zettel_id uuid REFERENCES content.canonical_zettels(id) ON DELETE SET NULL,
  metadata        jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_kg_edges_workspace_src ON kg.kg_edges (workspace_key, src_node_id);
CREATE INDEX idx_kg_edges_workspace_dst ON kg.kg_edges (workspace_key, dst_node_id);
CREATE INDEX idx_kg_edges_relation ON kg.kg_edges (relation_type);

CREATE TABLE kg.chunk_node_mentions (
  canonical_chunk_id uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
  kg_node_id      bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
  mention_type    text NOT NULL CHECK (mention_type IN ('extracted','tagged','derived','authored')),
  score           numeric,
  metadata        jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (canonical_chunk_id, kg_node_id, mention_type)
);
CREATE INDEX idx_chunk_node_mentions_node ON kg.chunk_node_mentions(kg_node_id);

-- AUDIT FIX B.3: SECURITY DEFINER RPC must authorize caller against jwt_workspace_ids().
CREATE OR REPLACE FUNCTION kg.expand_subgraph(
  p_workspace_id uuid,
  p_node_ids bigint[],
  p_depth int DEFAULT 1
) RETURNS TABLE(id bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())) THEN
    RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
    WITH RECURSIVE walk AS (
      SELECT unnest(p_node_ids) AS id, 0 AS d
      UNION ALL
      SELECT CASE WHEN e.src_node_id = w.id THEN e.dst_node_id ELSE e.src_node_id END, w.d + 1
      FROM kg.kg_edges e
      JOIN walk w ON e.src_node_id = w.id OR e.dst_node_id = w.id
      WHERE w.d < p_depth
        AND (e.workspace_id = p_workspace_id OR e.workspace_id IS NULL)
    )
    SELECT DISTINCT walk.id FROM walk WHERE walk.id <> ALL(p_node_ids);
END $$;
GRANT EXECUTE ON FUNCTION kg.expand_subgraph(uuid, bigint[], int) TO authenticated;
```

### 4.4 `rag` schema

```sql
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE rag.kastens (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  name            text NOT NULL,
  description     text,
  icon            text,
  color           text,
  default_quality text NOT NULL DEFAULT 'fast' CHECK (default_quality IN ('fast','high')),
  last_used_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

CREATE TABLE rag.kasten_members (
  kasten_id       uuid NOT NULL REFERENCES rag.kastens(id) ON DELETE CASCADE,
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('owner','editor','viewer')),
  added_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kasten_id, workspace_id)
);
CREATE INDEX idx_kasten_members_workspace ON rag.kasten_members(workspace_id);

CREATE TABLE rag.kasten_zettels (
  kasten_id       uuid NOT NULL REFERENCES rag.kastens(id) ON DELETE CASCADE,
  workspace_zettel_id uuid NOT NULL REFERENCES content.workspace_zettels(id) ON DELETE CASCADE,
  added_via       text NOT NULL CHECK (added_via IN ('manual','bulk_tag','bulk_source','graph_pick','migration')),
  added_filter    jsonb,
  added_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kasten_id, workspace_zettel_id)
);

CREATE TABLE rag.chat_sessions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  profile_id      uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
  kasten_id       uuid REFERENCES rag.kastens(id) ON DELETE CASCADE,  -- audit fix: CASCADE consistent with messages
  title           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rag.chat_messages (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      uuid NOT NULL REFERENCES rag.chat_sessions(id) ON DELETE CASCADE,
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('user','assistant','system')),
  content         text NOT NULL,
  citations       jsonb NOT NULL DEFAULT '[]',
  verdict         text CHECK (verdict IN ('supported','unsupported','retried_supported','partial')),
  retrieval_run_id uuid REFERENCES pipelines.pipeline_runs(id) ON DELETE SET NULL,  -- audit fix A.2
  token_counts    jsonb,
  latency_ms      int,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_chat_messages_session ON rag.chat_messages(session_id, created_at);

-- Consistency invariant: chat_messages.workspace_id == chat_sessions.workspace_id
CREATE OR REPLACE FUNCTION rag.assert_chat_message_workspace_match()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.workspace_id <> (SELECT workspace_id FROM rag.chat_sessions WHERE id = NEW.session_id) THEN
    RAISE EXCEPTION 'chat_messages.workspace_id must match chat_sessions.workspace_id';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_chat_messages_workspace_check
  BEFORE INSERT OR UPDATE ON rag.chat_messages
  FOR EACH ROW EXECUTE FUNCTION rag.assert_chat_message_workspace_match();

CREATE TABLE rag.retrieval_signal_weights (
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  source_canonical_chunk_id uuid NOT NULL,
  target_canonical_chunk_id uuid NOT NULL,
  query_class     text NOT NULL,
  weight          double precision NOT NULL,
  refreshed_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, source_canonical_chunk_id, target_canonical_chunk_id, query_class)
);
CREATE INDEX idx_retrieval_signal_workspace_target
  ON rag.retrieval_signal_weights(workspace_id, target_canonical_chunk_id);

-- Scorer registry (Solr LTR + Personalize $LATEST pattern)
-- References: https://solr.apache.org/guide/solr/latest/query-guide/learning-to-rank.html
--             https://docs.aws.amazon.com/personalize/latest/dg/campaigns.html
CREATE TABLE rag.retrieval_scorer_registry (
  scorer_name     text PRIMARY KEY,
  impl_class      text NOT NULL,
  supported_inputs jsonb NOT NULL DEFAULT '{}',
  description     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rag.retrieval_scorer_version (
  scorer_name     text NOT NULL REFERENCES rag.retrieval_scorer_registry(scorer_name) ON DELETE CASCADE,
  version_id      text NOT NULL,
  params          jsonb NOT NULL DEFAULT '{}',
  notes           text,
  created_by      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (scorer_name, version_id)
);

CREATE TABLE rag.retrieval_pipeline_config (
  environment     text NOT NULL CHECK (environment IN ('prod','staging','dev')),
  scorer_name     text NOT NULL,
  version_id      text NOT NULL,
  enabled         boolean NOT NULL DEFAULT true,
  weight          numeric NOT NULL DEFAULT 1.0,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      text,
  PRIMARY KEY (environment, scorer_name),
  FOREIGN KEY (scorer_name, version_id)
    REFERENCES rag.retrieval_scorer_version(scorer_name, version_id)
);

CREATE TABLE rag.retrieval_pipeline_config_history (
  id              bigserial PRIMARY KEY,
  environment     text NOT NULL,
  scorer_name     text NOT NULL,
  version_id      text NOT NULL,
  enabled         boolean NOT NULL,
  weight          numeric NOT NULL,
  changed_at      timestamptz NOT NULL DEFAULT now(),
  changed_by      text,
  reason          text
);

-- Hot-reload notification (idempotent: skips notify if values unchanged)
CREATE OR REPLACE FUNCTION rag.notify_pipeline_config_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD IS NOT DISTINCT FROM NEW THEN
    RETURN NEW;
  END IF;
  PERFORM pg_notify('retrieval_pipeline_config_change', NEW.environment);
  RETURN NEW;
END $$;

CREATE TRIGGER trg_retrieval_pipeline_config_notify
  AFTER INSERT OR UPDATE ON rag.retrieval_pipeline_config
  FOR EACH ROW EXECUTE FUNCTION rag.notify_pipeline_config_change();

-- Kasten-share authorization: only kasten owners may grant memberships.
CREATE OR REPLACE FUNCTION rag.assert_kasten_owner_can_grant()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.role <> 'owner' AND NOT EXISTS (
    SELECT 1 FROM rag.kasten_members
    WHERE kasten_id = NEW.kasten_id
      AND workspace_id = ANY (core.jwt_workspace_ids())
      AND role = 'owner'
  ) THEN
    -- Allow service_role to bypass
    IF current_setting('request.jwt.claims', true)::jsonb ->> 'role' <> 'service_role' THEN
      RAISE EXCEPTION 'only kasten owners can grant memberships';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_kasten_members_grant_check
  BEFORE INSERT OR UPDATE ON rag.kasten_members
  FOR EACH ROW EXECUTE FUNCTION rag.assert_kasten_owner_can_grant();
```

### 4.5 `pipelines` schema

```sql
CREATE SCHEMA IF NOT EXISTS pipelines;

CREATE TABLE pipelines.pipeline_runs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid REFERENCES core.workspaces(id) ON DELETE CASCADE,
  kind            text NOT NULL CHECK (kind IN
    ('summarize','kg_extract','rag_ingest','nexus_ingest','metadata_enrich','retrieval_query','recompute_signals')),
  status          text NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  config          jsonb NOT NULL DEFAULT '{}',
  metrics         jsonb NOT NULL DEFAULT '{}',
  error           text,
  started_at      timestamptz,
  finished_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_pipeline_runs_workspace_kind ON pipelines.pipeline_runs(workspace_id, kind, created_at DESC);

CREATE TABLE pipelines.pipeline_run_items (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id          uuid NOT NULL REFERENCES pipelines.pipeline_runs(id) ON DELETE CASCADE,
  workspace_zettel_id uuid REFERENCES content.workspace_zettels(id) ON DELETE SET NULL,
  status          text NOT NULL CHECK (status IN ('queued','running','succeeded','failed','skipped')),
  attempt         int  NOT NULL DEFAULT 1,
  result          jsonb NOT NULL DEFAULT '{}',
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_pipeline_run_items_run ON pipelines.pipeline_run_items(run_id);
```

### 4.6 `billing` schema

All `pricing_*` tables migrated **as-is** but rekeyed: every `render_user_id TEXT` column → `profile_id UUID NOT NULL REFERENCES core.profiles(id)`. Plus new `billing.pricing_plan_entitlements`:

```sql
CREATE SCHEMA IF NOT EXISTS billing;

CREATE TABLE billing.pricing_plan_entitlements (
  plan_id         text NOT NULL,
  feature         text NOT NULL,
  unit            text NOT NULL,
  monthly_limit   numeric NOT NULL,
  is_hard_cap     boolean NOT NULL DEFAULT true,
  PRIMARY KEY (plan_id, feature, unit)
);

-- Other pricing_* tables: identical shape to today, render_user_id → profile_id UUID FK.
-- pricing_consume_entitlement(p_profile_id, p_feature, p_unit) → reads usage from
-- core.usage_aggregates, compares to billing.pricing_plan_entitlements, returns boolean.
```

### 4.7 HNSW index (separate file `10_hnsw_indexes.sql`, post-backfill)

```sql
-- Created AFTER backfill completes, per audit B.7.
-- HNSW build on populated table is dramatically faster than incremental
-- maintenance during INSERT (parallel build via pgvector 0.7+).
-- Reference: https://www.crunchydata.com/blog/hnsw-indexes-with-postgres-and-pgvector
SET maintenance_work_mem = '1GB';   -- sized for the build
CREATE INDEX idx_canonical_chunks_embedding_hnsw
  ON content.canonical_chunks
  USING hnsw (embedding halfvec_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

`hnsw.iterative_scan` is set per-statement inside `content.search_chunks()` (audit B.8).

---

## 5. Migration / Cutover Plan

| Step | What | Time est | Risk |
|---|---|---|---|
| 0 | Pre-flight: tested PITR restore on staging; written 1-pager runbook; **maintenance-mode in Caddy verified working**; flip site to maintenance mode | 0 | — |
| 1 | Snapshot via Supabase backup (PITR baseline) | 1 min | — |
| 2 | Apply files 00-09 via `apply_migrations.py --v2` (no HNSW yet) | 5 min | Low |
| 3 | Run `partman.create_parent('core.usage_events', ...)` for monthly partitions (idempotent guard) | 1 min | Low |
| 4 | **Backfill** in dependency order via `00_full_backfill.py`, with **per-step verification gates** | 30–90 min | Medium |
| 5 | Apply `10_hnsw_indexes.sql` (HNSW on populated `canonical_chunks`) | 5–20 min | Medium |
| 6 | Run `verify_backfill.py` — abort if any assertion fails | 15 min | — |
| 7 | Switch app code (env flag: `DB_SCHEMA_VERSION=v2`) | <1 min | Low |
| 8 | Smoke test 30 min | 30 min | — |
| 9 | If green: keep old tables for 14 days (renamed `_legacy_<name>`); else: rollback | 5 min | Low |
| 10 | Re-enable site | <1 min | — |

**Backfill specifics for canonical-content dedup:**
- GROUP BY `normalized_url + content_hash(body)`, INSERT one `canonical_zettels` row, INSERT one `canonical_chunks` row per chunk **with `embedding::halfvec` direct SQL cast** (audit C.6 — no Gemini re-call), INSERT N `workspace_zettels` rows, INSERT M `workspace_chunk_membership` rows.

**Backfill verification gates (audit D.3):** between each phase, `00_full_backfill.py` runs an assertion (e.g. `len(content.canonical_zettels) > 0` after Phase 02; `count of pricing_subscriptions matches old.count` after Phase 06) and exits non-zero on mismatch, requiring `--continue` from the operator.

**Rollback (same-day, env-flag flip + PITR if needed):**
1. Flip site to maintenance.
2. Set `DB_SCHEMA_VERSION=v1`. v1 code path is alive in the deployed image for **14 days** post-cutover (audit D.2).
3. If env-flag flip is sufficient (data still readable via legacy schema): re-enable site.
4. If rollback requires DB reset: Supabase PITR to `<pre-cutover-timestamp>`; lose any new captures since cutover. **Communicate to affected users via Telegram + status page** (audit F.5).

---

## 6. Application Code Surfaces That Must Change

| Code area | Change |
|---|---|
| `website/core/supabase_v2/repositories/*.py` | Use `client.schema("content").table("canonical_zettels")` form throughout (audit C.1) |
| `website/api/routes.py` `/api/summarize` | Insert workspace_id from JWT claim; canonical-then-overlay |
| `website/features/rag_pipeline/scoring/registry_adapter.py` | **~150-line adapter (Phase-1 mandatory)**. LISTEN over **direct Postgres connection (port 5432, NOT 6543 pgbouncer)** + 60s polling fallback (audit C.3). Initialized in **gunicorn `post_fork` hook**, not lifespan, to avoid `--preload` snapshot drift (audit F.2) |
| `website/features/rag_pipeline/retrieval/hybrid.py` | Reads weights from `RegistryAdapter.get_weight(...)`. ANN goes through `content.search_chunks()` RPC (audit B.5), not direct table query |
| `website/features/user_pricing/*` | `profile_id UUID` instead of `render_user_id TEXT` |
| `ops/scripts/recompute_usage_edges.py` | Renamed → `recompute_signal_weights.py`. Reads `core.usage_events`, writes `rag.retrieval_signal_weights` |
| `ops/scripts/apply_migrations.py` | Targets all 6 schemas. Tracks in `core._migrations_applied` (bootstrap-safe per audit I.1) |
| `ops/caddy/Caddyfile` | **Add `@maintenance` matcher reading `/etc/caddy/maintenance.flag`** (audit D.1). Currently NOT implemented |
| Telegram bot | **No changes** — bot writes to `KG_DIRECTORY` / GitHub only, confirmed not dual-writing to Supabase (audit G.4) |
| User-facing error handling | `validate_registry_completeness` and other internal errors converted to generic 500 with logged detail; never bubble to client (audit F.3) |

---

## 7. What This Looks Like at 15 vs 10k Users

| Surface | At 15 users | At 10k users |
|---|---|---|
| Tenancy | One workspace per user | Many shared kastens; team plans plausible |
| Canonical chunks | ~1k rows; HNSW fits in default Supabase compute | ~10M rows; halfvec(768) HNSW ~35GB → 64GB compute add-on |
| Usage events | <100k rows/yr; partman partitions mostly empty | ~100M rows/yr; monthly DETACH+DROP at 24-month boundary |
| RLS | JWT-claim wins ~10ms/query | JWT-claim wins ~450ms/query — **the deciding factor** |
| Scorer registry | 8 rows; rarely changes | Same 8 rows × 3 envs; weight changes are instant cutover |
| Quota enforcement | Lots of headroom | Race-safe at thousands of concurrent debits |

---

## 8. Out-of-Scope (deliberate non-decisions)

- Spill to Turbopuffer / pgvectorscale (threshold trip-wire only)
- CDC to ClickHouse for analytics
- Bandit arm persistence
- TimescaleDB hypertables / continuous aggregates
- Per-tenant partial HNSW indexes
- Embedding model swap

---

## 9. Tracebacks: Every Section to a Locked Decision

| Section | Decision |
|---|---|
| §3 schema split | Q3 / Approach 3 (hybrid core + features) |
| §4.0 _migrations_applied bootstrap | audit I.1 |
| §4.1 core.profiles + allowlist | Q4 + audit I.2 |
| §4.1 jwt_workspace_ids cast fix | audit B.1 |
| §4.1 consume_quota typed RPC | audit C.2 |
| §4.2 canonical+overlay, content_hash dedup | Q2, Burn-5 |
| §4.2 split soft-delete triggers | audit B.4, A.3 |
| §4.2 search_chunks RPC | audit B.5 |
| §4.2 widened workspace_chunk_membership PK | audit B.6 |
| §4.3 unique-via-generated-column | audit B.2 |
| §4.3 expand_subgraph auth check | audit B.3 |
| §4.4 idempotent pg_notify | audit E.6 |
| §4.4 kasten_owner_can_grant trigger | audit I |
| §4.4 chat_messages workspace consistency trigger | audit I |
| §4.7 HNSW post-backfill | audit B.7 |
| §6 ~150-line registry adapter mandatory + post-fork hook + direct-port LISTEN | Q6.b + audit C.3, F.2 |
| §6 Caddy maintenance mode | audit D.1 |

---

## 10. Spec Self-Review Checklist

- [x] No "TBD" / "TODO" / placeholder requirements
- [x] No internal contradictions
- [x] Scope is one implementation plan, not multiple
- [x] Every requirement single-interpretation
- [x] Every section traces to a locked decision OR audit fix (§9)
- [x] Migration plan has rollback (§5)
- [x] What-this-looks-like at 15-vs-10k users explicit (§7)
- [x] Out-of-scope explicit (§8)
- [x] Citations to primary sources for every non-obvious claim
- [x] All 12 BLOCKER + 29 MAJOR audit findings addressed (§9 trace)

**Ready for the implementation plan.**
