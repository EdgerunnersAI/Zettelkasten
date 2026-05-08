# DB Refactor Design — 2026-05-08

**Status:** Brainstorming complete; locked decisions baked in; ready for implementation plan.
**Scope:** Full Supabase schema redesign for the Zettelkasten website. Bot stays out of scope (writes to local `KG_DIRECTORY` / GitHub, not Supabase).
**Cutover:** Big-bang weekend window, 2-4 hours, same-day PITR rollback acceptable.
**User scale today:** 10–15 users. **Target:** 10,000+ users without breaking anything along the way.

---

## 1. Why

The current Supabase schema (7 modules: `kg_public`, `kg_features`, `rag_chatbot`, `summarization_engine`, `nexus`, `user_auth`, `user_pricing`) was verified by an architecture-scout and has five structural problems:

1. **No workspace abstraction.** Every hot table keys on `user_id`; sharing is impossible without duplicating content.
2. **Three parallel views over content** (KG, RAG, summarization) with **no canonical Document/Zettel record**. Same URL captured by two users = two `kg_nodes` rows + two embeddings + two HNSW entries.
3. **RLS uses the documented 10× anti-pattern** (`EXISTS (SELECT 1 FROM kg_users u …)`) on every policy.
4. **Usage accounting is fragmented across 16+ tables** in 3 schemas; no source of truth for "how much has this user/workspace consumed".
5. **Every retrieval iteration adds 1–3 schema artifacts.** Some die (RES-2 `kg_kasten_node_freq` floor never crossed); some replace others (`rag_kasten_chunk_counts` replaced kasten_freq).

This design fixes all five with industry-validated patterns, sized for 10k users while staying lightweight at 15.

Full research: 6 cited research notes (multi-tenant Postgres, RAG+KG dedup, usage events, dynamic schemas, pgvector, scorer registry) summarized in conversation transcript 2026-05-08.

---

## 2. Locked Decisions (from /brainstorming)

| # | Decision | Source |
|---|---|---|
| Q1 | **Workspace tenancy** (workspace_id, not user_id). Personal-workspace-per-user, invisible UI today, exposable later (Linear-style). Sharing granularity: Zettel + Kasten | Slack/Notion/Linear consensus |
| Q1.b | **Kasten sharing role model**: `kasten_members.role IN ('owner','editor','viewer')`. Default share = viewer; promotable | Notion/Linear |
| Q2 | **Auto-dedup by content_hash** across workspaces. Canonical key = normalized URL + content_hash(body). Per-workspace overlay holds tags/summary/notes | AWS pool pattern, Microsoft Azure RAG |
| Q2.b | **Delete = remove membership; reaper shreds canonical row 7 days after last reference leaves** | Notion/Roam default |
| Q3 | **Big-bang weekend cutover.** Same-day PITR rollback. Schema still expand-contract-friendly for future migrations | User constraint |
| Q4 | **Full migration to `core.profiles`** (`profiles.id REFERENCES auth.users(id)`). Purge Render entirely. JWT custom claim `workspace_ids[]` in `auth.users.raw_app_meta_data`. **Eager** JWT refresh via Postgres trigger on membership change | Supabase Custom Claims |
| Q5 | **Single `core.usage_events` fact table** for billing+analytics, partitioned by `occurred_at` via pg_partman. **Retrieval signals stay narrow** in `rag.retrieval_signal_weights` (denormalized aggregate) | Stripe/Lago/OpenMeter pattern |
| Q5.b | **Quota = soft-check in app + hard-enforce in DB.** Atomic `UPDATE quotas SET remaining = remaining - 1 WHERE workspace_id = $1 AND remaining > 0 RETURNING` | Race-condition literature |
| Q6 | **Hybrid scorer registry** (Solr LTR + Personalize `$LATEST` model): code for impl, data for tunable knobs. Four-table registry. Prompts/pipeline-versions stay as YAML in git (GraphRAG/DSPy/Promptfoo). Bandit persistence deferred | Solr LTR, AWS Personalize, Stitch Fix |
| Q6.b | **MANDATORY Phase-1 deliverable**: ~150-line Python adapter so each scorer reads from the registry at boot + on `pg_notify` hot-reload. Without it the registry is decorative | — |
| Burn-3 | **halfvec(768)** at cutover (50% storage + index reduction at near-zero recall loss for 768-d) | Neon, Supabase |
| Burn-4 | **`embedding_model_version` column** on canonical_chunks from day 1 | TianPan, Google Cloud |

---

## 3. Architecture: Six Schemas

```
core ← content ← kg ← rag
core ← pipelines
core ← billing
```

**FK direction is strictly downward.** No cycles. `core` references nothing outside itself. `rag` may FK into `kg`, `content`, `core`.

| Schema | Tables (table count) |
|---|---|
| **core** | `profiles`, `workspaces`, `workspace_members`, `usage_events`, `usage_aggregates`, `quotas`, `soft_delete_queue` (7) |
| **content** | `canonical_zettels`, `canonical_chunks`, `workspace_zettels`, `workspace_chunk_membership`, `embedding_model_versions` (5) |
| **kg** | `kg_nodes`, `kg_edges`, `chunk_node_mentions` (3) |
| **rag** | `kastens`, `kasten_members`, `kasten_zettels`, `chat_sessions`, `chat_messages`, `retrieval_signal_weights`, `retrieval_scorer_registry`, `retrieval_scorer_version`, `retrieval_pipeline_config`, `retrieval_pipeline_config_history` (10) |
| **pipelines** | `pipeline_runs`, `pipeline_run_items` (2) |
| **billing** | `pricing_billing_profiles`, `pricing_orders`, `pricing_subscriptions`, `pricing_webhook_events`, `pricing_credit_ledger`, `pricing_balances`, `pricing_payment_events`, `pricing_plan_cache`, `pricing_refunds`, `pricing_disputes`, `pricing_plan_entitlements` (11) |

**Total: 38 tables.** Today's schema has ~28 (counting all `kg_features` ALTERs). Net +10, but **1 unified events table** replaces ~16 fragmented ones.

**Retired:**
- `kg_public.kg_users` → `core.profiles`
- `kg_public.kg_kasten_node_freq` → not migrated (dead surface)
- `kg_public.kg_usage_edges` + `kg_usage_edges_agg` MV → replaced by `core.usage_events` projecting into `rag.retrieval_signal_weights`
- `nexus` schema → absorbed into `pipelines.pipeline_runs` with `kind='nexus_ingest'`
- `user_auth` schema → trigger lives directly in `core`
- `kg_features` schema → RPCs absorbed into `kg`; ALTERs collapsed into base `kg_nodes` definition

---

## 4. Schema DDL

### 4.1 `core` schema

```sql
CREATE SCHEMA IF NOT EXISTS core;

-- Identity (replaces kg_users; FK to Supabase auth.users)
CREATE TABLE core.profiles (
  id              uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name    text,
  email           text,
  avatar_url      text,
  razorpay_subscriber_id text UNIQUE,        -- For pricing webhook lookup
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
CREATE TABLE core.usage_events (
  id              bigserial,
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  profile_id      uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
  feature         text NOT NULL,                 -- 'rag_chat','summarize','kg_extract','share_invite','feature_X_clicked',...
  unit            text NOT NULL,                 -- 'tokens_in','tokens_out','messages','queries','calls'
  quantity        numeric NOT NULL DEFAULT 1,
  metadata        jsonb NOT NULL DEFAULT '{}',
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (occurred_at, id)
) PARTITION BY RANGE (occurred_at);

-- pg_partman maintains partitions: monthly going forward, retain 24 months
-- Initial partitions created in migration script; partman_create_parent() sets up automation

CREATE INDEX idx_usage_events_workspace_feature_time
    ON core.usage_events (workspace_id, feature, occurred_at DESC);
CREATE INDEX idx_usage_events_profile_time
    ON core.usage_events (profile_id, occurred_at DESC);

-- Aggregate rollup (trigger-maintained summary table per Tiger Data CAGG-style pattern)
CREATE TABLE core.usage_aggregates (
  workspace_id    uuid NOT NULL,
  profile_id      uuid NOT NULL,
  feature         text NOT NULL,
  unit            text NOT NULL,
  period_start    timestamptz NOT NULL,        -- truncated to billing month
  quantity_total  numeric NOT NULL DEFAULT 0,
  events_count    bigint  NOT NULL DEFAULT 0,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, feature, unit, period_start)
);

-- Quotas: hard-enforce target. UPDATE-with-RETURNING pattern (race-safe).
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
  table_name      text NOT NULL,             -- 'content.canonical_zettels' or 'content.canonical_chunks'
  row_id          uuid NOT NULL,
  enqueued_at     timestamptz NOT NULL DEFAULT now(),
  shred_after     timestamptz NOT NULL,      -- enqueued_at + INTERVAL '7 days'
  shredded_at     timestamptz
);
CREATE INDEX idx_soft_delete_pending ON core.soft_delete_queue(shred_after) WHERE shredded_at IS NULL;

-- JWT-claim helper. Reads workspace_ids[] from JWT, falls back to membership table for service-role.
CREATE OR REPLACE FUNCTION core.jwt_workspace_ids() RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT COALESCE(
      (auth.jwt() -> 'app_metadata' -> 'workspace_ids')::jsonb::text::uuid[],
      ARRAY[]::uuid[]
    );
$$;

-- Eager JWT refresh trigger: when membership changes, force re-issue on next request.
-- Implementation: write to auth.users.raw_app_meta_data so next session JWT picks it up.
-- For zero-lag: short JWT TTL (5 min) + client auto-refresh.
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
    VALUES (NEW.id, 'Personal', true)
    RETURNING id INTO ws_id;
  INSERT INTO core.workspace_members (workspace_id, profile_id, role)
    VALUES (ws_id, NEW.id, 'owner');
  RETURN NEW;
END $$;

CREATE TRIGGER trg_profile_personal_workspace
  AFTER INSERT ON core.profiles
  FOR EACH ROW EXECUTE FUNCTION core.create_personal_workspace();

-- Sync auth.users → core.profiles on signup (replaces today's user_auth/schema.sql trigger)
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
```

**RLS pattern (every table in every feature schema):**

```sql
-- Generic shape. Replace <tbl>.workspace_id with table-specific column.
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

This is the **10× perf win** vs today's `EXISTS (SELECT 1 FROM kg_users …)` — JWT lookup, no JOIN, no disk I/O for membership check.

### 4.2 `content` schema (canonical + per-workspace overlay)

```sql
CREATE SCHEMA IF NOT EXISTS content;

-- Embedding model registry (for future cutover; lets v1 + v2 coexist)
CREATE TABLE content.embedding_model_versions (
  version_id      text PRIMARY KEY,            -- e.g. 'gemini-001-mrl-768', 'text-embedding-3-large'
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
  content_hash    bytea NOT NULL,              -- sha256 of body
  source_type     text NOT NULL CHECK (source_type IN
                    ('youtube','reddit','github','twitter','substack','newsletter','medium','web','generic')),
  title           text,
  body_md         text,                        -- Original captured content (TOAST-stored)
  publication_date date,
  source_metadata jsonb NOT NULL DEFAULT '{}', -- channel, author, etc.
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
  embedding       halfvec(768),                 -- ⚠️ halfvec, not vector — 50% storage + index win
  embedding_model_version text NOT NULL DEFAULT 'gemini-001-mrl-768'
                  REFERENCES content.embedding_model_versions(version_id),
  fts             tsvector,
  metadata        jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (canonical_zettel_id, chunk_idx)
);

-- HNSW on the SHARED canonical embedding (fixes today's missing-index gap on chunks)
CREATE INDEX idx_canonical_chunks_embedding_hnsw
  ON content.canonical_chunks
  USING hnsw (embedding halfvec_cosine_ops)
  WITH (m = 16, ef_construction = 64);
SET hnsw.iterative_scan = relaxed_order;        -- pgvector 0.8+; prevents empty-result on tenant filter

CREATE INDEX idx_canonical_chunks_fts ON content.canonical_chunks USING GIN (fts);
CREATE INDEX idx_canonical_chunks_zettel ON content.canonical_chunks(canonical_zettel_id);

-- FTS trigger same as today
CREATE OR REPLACE FUNCTION content.canonical_chunks_fts_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.fts := to_tsvector('english', coalesce(NEW.content,'')); RETURN NEW; END $$;
CREATE TRIGGER trg_canonical_chunks_fts
  BEFORE INSERT OR UPDATE OF content ON content.canonical_chunks
  FOR EACH ROW EXECUTE FUNCTION content.canonical_chunks_fts_update();

-- Per-workspace OVERLAY: tags, AI summary, user notes, hit_count.
-- This is the row that "owns" the Zettel from a tenant's perspective.
CREATE TABLE content.workspace_zettels (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  canonical_zettel_id uuid NOT NULL REFERENCES content.canonical_zettels(id) ON DELETE RESTRICT,
  ai_summary      text,
  ai_summary_engine_version text,              -- which summarizer ran
  user_tags       text[] NOT NULL DEFAULT '{}',
  user_note       text,
  pinned          boolean NOT NULL DEFAULT false,
  added_via       text NOT NULL CHECK (added_via IN ('telegram','website','share','migration')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz,                 -- soft-delete; reaper clears at last reference
  UNIQUE (workspace_id, canonical_zettel_id)
);
CREATE INDEX idx_workspace_zettels_workspace_tags
  ON content.workspace_zettels USING GIN (user_tags);
CREATE INDEX idx_workspace_zettels_workspace_created
  ON content.workspace_zettels (workspace_id, created_at DESC) WHERE deleted_at IS NULL;

-- Per-workspace chunk membership (lets us narrow ANN to a workspace's chunks via JOIN)
CREATE TABLE content.workspace_chunk_membership (
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  canonical_chunk_id uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
  workspace_zettel_id uuid NOT NULL REFERENCES content.workspace_zettels(id) ON DELETE CASCADE,
  hit_count       int NOT NULL DEFAULT 0,      -- per-Kasten hit-count (replaces kasten_freq, alive at the membership layer)
  last_hit_at     timestamptz,
  PRIMARY KEY (workspace_id, canonical_chunk_id)
);
CREATE INDEX idx_workspace_chunks_workspace ON content.workspace_chunk_membership(workspace_id);

-- Soft-delete reaper trigger: when last membership leaves, enqueue canonical for shred
CREATE OR REPLACE FUNCTION content.enqueue_canonical_shred_if_orphan()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM content.workspace_zettels wz
    WHERE wz.canonical_zettel_id = OLD.canonical_zettel_id
      AND wz.deleted_at IS NULL
  ) THEN
    INSERT INTO core.soft_delete_queue (table_name, row_id, shred_after)
      VALUES ('content.canonical_zettels', OLD.canonical_zettel_id, now() + INTERVAL '7 days');
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER trg_workspace_zettel_orphan_check
  AFTER DELETE OR UPDATE OF deleted_at ON content.workspace_zettels
  FOR EACH ROW WHEN (OLD.deleted_at IS NULL AND (NEW IS NULL OR NEW.deleted_at IS NOT NULL))
  EXECUTE FUNCTION content.enqueue_canonical_shred_if_orphan();
```

### 4.3 `kg` schema (entity layer + RAG↔KG bridge)

```sql
CREATE SCHEMA IF NOT EXISTS kg;

-- KG nodes are GLOBAL (entities/topics/concepts). user-specific facts live as edges in the workspace's overlay.
-- Workspace-scoped tagging lives on content.workspace_zettels.user_tags.
CREATE TABLE kg.kg_nodes (
  id              bigserial PRIMARY KEY,
  workspace_id    uuid REFERENCES core.workspaces(id) ON DELETE CASCADE, -- NULL = global node
  type            text NOT NULL,                -- 'entity','concept','tag','person','org','topic'
  canonical_name  text NOT NULL,
  slug            text NOT NULL,
  metadata        jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_kg_nodes_workspace_slug
  ON kg.kg_nodes (COALESCE(workspace_id::text,'__global__'), slug);
CREATE INDEX idx_kg_nodes_canonical_name ON kg.kg_nodes(canonical_name);

-- Edges: typed relations between KG nodes
CREATE TYPE kg.kg_edge_relation AS ENUM ('shared_tag','cites','mentions','co_occurs','authored_by','published_in');

CREATE TABLE kg.kg_edges (
  id              bigserial PRIMARY KEY,
  workspace_id    uuid REFERENCES core.workspaces(id) ON DELETE CASCADE, -- NULL = global edge
  src_node_id     bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
  dst_node_id     bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
  relation_type   kg.kg_edge_relation NOT NULL,
  weight          numeric,
  evidence_canonical_zettel_id uuid REFERENCES content.canonical_zettels(id) ON DELETE SET NULL,
  metadata        jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_kg_edges_workspace_src ON kg.kg_edges (COALESCE(workspace_id::text,'__global__'), src_node_id);
CREATE INDEX idx_kg_edges_workspace_dst ON kg.kg_edges (COALESCE(workspace_id::text,'__global__'), dst_node_id);
CREATE INDEX idx_kg_edges_relation ON kg.kg_edges (relation_type);

-- THE BRIDGE: chunk ↔ kg_node mentions (replaces today's slug-based soft FK + scattered RPCs)
CREATE TABLE kg.chunk_node_mentions (
  canonical_chunk_id uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
  kg_node_id      bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
  mention_type    text NOT NULL CHECK (mention_type IN ('extracted','tagged','derived','authored')),
  score           numeric,
  metadata        jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (canonical_chunk_id, kg_node_id, mention_type)
);
CREATE INDEX idx_chunk_node_mentions_node ON kg.chunk_node_mentions(kg_node_id);

-- BFS RPC for graph-aware retrieval (replaces today's kg_expand_subgraph)
CREATE OR REPLACE FUNCTION kg.expand_subgraph(
  p_workspace_id uuid,
  p_node_ids bigint[],
  p_depth int DEFAULT 1
) RETURNS TABLE(id bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  WITH RECURSIVE walk AS (
    SELECT unnest(p_node_ids) AS id, 0 AS d
    UNION ALL
    SELECT CASE WHEN e.src_node_id = w.id THEN e.dst_node_id ELSE e.src_node_id END,
           w.d + 1
    FROM kg.kg_edges e
    JOIN walk w ON e.src_node_id = w.id OR e.dst_node_id = w.id
    WHERE w.d < p_depth
      AND (e.workspace_id = p_workspace_id OR e.workspace_id IS NULL)
  )
  SELECT DISTINCT id FROM walk WHERE id <> ALL(p_node_ids);
$$;
```

### 4.4 `rag` schema (retrieval surface + scorer registry)

```sql
CREATE SCHEMA IF NOT EXISTS rag;

-- Kastens (rebrand of rag_sandboxes). Workspace-scoped curated Zettel collections.
CREATE TABLE rag.kastens (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  name            text NOT NULL,
  description     text,
  icon            text,
  color           text,                          -- always teal in UI per project rule
  default_quality text NOT NULL DEFAULT 'fast' CHECK (default_quality IN ('fast','high')),
  last_used_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

-- Kasten members: cross-workspace access (Q1: viewer/editor/owner roles)
CREATE TABLE rag.kasten_members (
  kasten_id       uuid NOT NULL REFERENCES rag.kastens(id) ON DELETE CASCADE,
  workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('owner','editor','viewer')),
  added_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kasten_id, workspace_id)
);
CREATE INDEX idx_kasten_members_workspace ON rag.kasten_members(workspace_id);

-- Zettels in a kasten (FK to workspace_zettels — the per-tenant overlay)
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
  kasten_id       uuid REFERENCES rag.kastens(id) ON DELETE SET NULL,
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
  citations       jsonb NOT NULL DEFAULT '[]',  -- [{canonical_chunk_id, score}, ...]
  verdict         text CHECK (verdict IN ('supported','unsupported','retried_supported','partial')),
  retrieval_run_id uuid,                        -- FK to pipelines.pipeline_runs
  token_counts    jsonb,
  latency_ms      int,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_chat_messages_session ON rag.chat_messages(session_id, created_at);

-- Retrieval signal weights: replaces kg_usage_edges_agg. Hot read path: scoring layer reads this directly.
-- Maintained by an event consumer (cron job) reading core.usage_events with feature='node_cited' / 'verdict_supported'.
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
CREATE TABLE rag.retrieval_scorer_registry (
  scorer_name     text PRIMARY KEY,             -- 'bm25','dense','rrf','anti_magnet','graph_score','entity_anchor','cross_encoder'
  impl_class      text NOT NULL,                -- Python dotted path
  supported_inputs jsonb NOT NULL DEFAULT '{}',
  description     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rag.retrieval_scorer_version (
  scorer_name     text NOT NULL REFERENCES rag.retrieval_scorer_registry(scorer_name) ON DELETE CASCADE,
  version_id      text NOT NULL,                -- e.g. 'v2026-05-08' or git sha
  params          jsonb NOT NULL DEFAULT '{}',  -- {"floor":50, "exponent":0.5, ...}
  notes           text,
  created_by      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (scorer_name, version_id)
);

-- Active config per environment (Personalize $LATEST style — pointer-with-version)
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

-- Append-only audit (never UPDATE — INSERT a new row to "change" config)
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

-- Hot-reload notification: when retrieval_pipeline_config changes, broadcast pg_notify
CREATE OR REPLACE FUNCTION rag.notify_pipeline_config_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM pg_notify('retrieval_pipeline_config_change', NEW.environment); RETURN NEW; END $$;

CREATE TRIGGER trg_retrieval_pipeline_config_notify
  AFTER INSERT OR UPDATE ON rag.retrieval_pipeline_config
  FOR EACH ROW EXECUTE FUNCTION rag.notify_pipeline_config_change();
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
  config          jsonb NOT NULL DEFAULT '{}', -- engine_version, prompt_version, etc. Read from YAML in git
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

All `pricing_*` tables migrated **as-is** but rekeyed: every `render_user_id TEXT` column → `profile_id UUID NOT NULL REFERENCES core.profiles(id)`. No schema design change beyond the rekey + adding `pricing_plan_entitlements` (replaces today's hardcoded `pricing_plan_cap()` IF-branches).

```sql
CREATE SCHEMA IF NOT EXISTS billing;

-- (Most pricing_* tables are mechanical rekeys. Showing the new entitlements table.)
CREATE TABLE billing.pricing_plan_entitlements (
  plan_id         text NOT NULL,
  feature         text NOT NULL,                -- matches core.usage_events.feature
  unit            text NOT NULL,                -- matches core.usage_events.unit
  monthly_limit   numeric NOT NULL,
  is_hard_cap     boolean NOT NULL DEFAULT true,
  PRIMARY KEY (plan_id, feature, unit)
);

-- billing.pricing_subscriptions, pricing_credit_ledger, pricing_balances, etc:
-- same shape as today, render_user_id → profile_id, otherwise unchanged.
```

---

## 5. Migration / Cutover Plan

**Sequence (single weekend, 2-4 hours, in one transaction where possible):**

| Step | What | Time est | Risk |
|---|---|---|---|
| 0 | Pre-flight: tested PITR restore on staging; written 1-pager runbook; flip site to maintenance mode | 0 (done before window) | — |
| 1 | Snapshot via Supabase backup (PITR baseline) | 1 min | — |
| 2 | `CREATE SCHEMA core, content, kg, rag, pipelines, billing;` | <1 min | Low |
| 3 | Run all CREATE TABLE / TRIGGER / FUNCTION DDL from §4 | 2–5 min | Low |
| 4 | Run `pg_partman.create_parent('core.usage_events', ...)` for monthly partitions | 1 min | Low |
| 5 | **Backfill** in dependency order: profiles ← workspaces (auto-trigger) ← canonical_zettels ← canonical_chunks ← workspace_zettels ← workspace_chunk_membership ← kg_nodes ← kg_edges ← chunk_node_mentions ← kastens ← chat_sessions/messages ← billing.* ← retrieval_scorer_registry seed | 30–90 min | Medium (data correctness) |
| 6 | Build HNSW index on `content.canonical_chunks.embedding` (halfvec, m=16, ef_construction=64) | 5–20 min depending on row count | Medium (memory pressure) |
| 7 | Verify: row counts match expectations; spot-check sharing semantics; run RAG smoke test | 15 min | — |
| 8 | Switch app code to new schemas (env flag: `DB_SCHEMA_VERSION=v2`) | <1 min (config push) | Low |
| 9 | Smoke test prod for 30 min | 30 min | — |
| 10 | If green: drop old tables (`kg_users`, `kg_nodes`, `kg_links`, `kg_node_chunks`, `rag_sandboxes`, `rag_sandbox_members`, `chat_sessions`, `chat_messages`, `summary_batch_runs`, etc.) | 5 min | Low (PITR if needed) |
| 11 | Re-enable site | <1 min | — |

**Backfill specifics for canonical-content dedup:**
- Today's `kg_nodes` has one row per (user, slug). Multiple users with same URL = duplicate rows.
- Backfill script: GROUP BY `normalized_url + content_hash(body)`, INSERT one `canonical_zettels` row, INSERT one `canonical_chunks` row per chunk, INSERT N `workspace_zettels` rows (one per user who had captured it), INSERT N `workspace_chunk_membership` rows.
- Verify: `SUM(workspace_zettels.count) == old kg_nodes.count`; `canonical_zettels.count <= old kg_nodes.count`.

**Backfill specifics for usage_events:**
- Today's `kg_usage_edges` rows → INSERT into `core.usage_events` with `feature='retrieval_signal_emit'`, then run a one-shot job that aggregates them into `rag.retrieval_signal_weights`.
- Today's `pricing_credit_ledger` rows → INSERT into `core.usage_events` with `feature='pricing_credit_*'`.
- Today's `pricing_usage_counters` rows → INSERT into `core.usage_aggregates`.

**Rollback (same-day, PITR):**
1. Flip site to maintenance.
2. `supabase db reset --to <pre-cutover-timestamp>` (Supabase PITR).
3. Flip env flag back to `DB_SCHEMA_VERSION=v1`.
4. Re-enable site.
5. Lose any new captures since cutover (acceptable per Q3).

---

## 6. Application Code Surfaces That Must Change

| Code area | Change |
|---|---|
| `website/core/supabase_kg/repository.py` | Rewrite to target new `content`/`kg`/`rag` schemas; the `KGRepository.add_node` becomes `upsert_canonical_zettel + add_workspace_overlay` |
| `website/api/routes.py` `/api/summarize` | Insert workspace_id from JWT claim (the request now carries `app_metadata.workspace_ids[]`); insert canonical-then-overlay |
| `website/features/rag_pipeline/retrieval/*` | **`~150-line scorer-registry adapter` (Phase-1 mandatory)**. Each scorer's `__init__` reads from `rag.retrieval_pipeline_config` at boot; subscribes to `pg_notify('retrieval_pipeline_config_change')`; rebuilds local config on notification |
| `website/features/rag_pipeline/retrieval/hybrid.py` | Replace hardcoded constants with values from registry adapter |
| `website/features/user_pricing/*` | Functions take `profile_id UUID` instead of `render_user_id TEXT`; webhook handler maps `razorpay_subscriber_id → profile_id` via `core.profiles.razorpay_subscriber_id` lookup |
| `website/features/api_key_switching/*` | Unaffected (no DB changes) |
| `ops/scripts/recompute_usage_edges.py` | Rewrite to read `core.usage_events` with `feature='retrieval_signal_emit'`, aggregate into `rag.retrieval_signal_weights`. The `kg_usage_edges_agg` MV is replaced by this scheduled job (same cron schedule) |
| `ops/scripts/apply_migrations.py` | Targets all 6 schemas; tracks per-schema migration state in `core._migrations_applied` (consolidated from today's per-schema tables) |
| Telegram bot | **No changes** (writes to `KG_DIRECTORY` / GitHub, not Supabase) |

**Test surface:**
- New unit tests for canonical-content dedup (`test_canonical_zettels.py`).
- New integration tests for sharing flows (`test_kasten_sharing.py`).
- New tests for JWT-claim RLS (verify cross-workspace access is denied).
- Existing 530+ tests must pass with the new repository layer.
- New scorer-registry adapter tests (config change → adapter re-loads → next request uses new weight).

---

## 7. What This Looks Like at 15 vs 10k Users

| Surface | At 15 users | At 10k users |
|---|---|---|
| Tenancy | One `workspaces` row per user; sharing rare; JWT claim has 1 workspace_id | Many shared kastens; JWT has avg 2-5 workspace_ids; team plans plausible |
| Canonical chunks | ~1k rows; HNSW fits in default Supabase compute | ~10M rows; halfvec(768) HNSW ~35GB → 64GB compute add-on; iterative_scan filters tenant subset |
| Usage events | <100k rows/year; partman partitions are mostly empty | ~100M rows/year; monthly DETACH+DROP at 24-month retention boundary |
| RLS | JWT-claim RLS wins ~10ms/query (still fast at any scale) | JWT-claim RLS wins ~450ms/query (per Supabase benchmark) — **the deciding factor** |
| Scorer registry | 7 scorer rows; rarely changed; `pg_notify` fires once a week | 7 scorer rows × 3 environments; weight changes during ops triage are instant cutover, no redeploy |
| Quota enforcement | Atomic UPDATE always succeeds (lots of headroom) | Race-safe at thousands of concurrent quota checks |
| Migration cost | This weekend = manageable | If we deferred this, Q3 weekend cutover wouldn't be possible |

---

## 8. Out-of-Scope (deliberate non-decisions)

- **Spill to Turbopuffer / pgvectorscale.** Threshold-based decision, not now. Build threshold trip-wire only.
- **CDC to ClickHouse for analytics.** Industry-standard but overkill at <1k users.
- **Bandit arm persistence.** Stitch Fix doesn't persist allocation; YAGNI until in-process state loss hurts in prod.
- **TimescaleDB hypertables / continuous aggregates.** Native Postgres partitioning is sufficient; can swap later.
- **Per-tenant partial HNSW indexes.** Single shared HNSW + iterative_scan post-filter is the right choice at 10k tenants.
- **Embedding model swap.** Schema supports it (embedding_model_versions table + embedding_model_version FK column); actual swap is a separate iteration.

---

## 9. Tracebacks: Every Section to a Locked Decision

| Section | Decision |
|---|---|
| §3 schema split | Q3 / Approach 3 (hybrid core + features) |
| §4.1 core.profiles, JWT-claim RLS | Q4 |
| §4.1 workspaces, members, auto-personal-workspace | Q1 |
| §4.1 usage_events partitioned, soft+hard quota | Q5 |
| §4.2 canonical+overlay, content_hash dedup, soft-delete reaper, 7-day window | Q2, Burn-5 |
| §4.2 halfvec(768), embedding_model_versions, missing-HNSW-on-chunks fix | Burn-3, Burn-4, scout-finding-#2 |
| §4.4 kasten_members.role | Q1.b |
| §4.4 retrieval_signal_weights | Q5 (separate from usage_events for hot path) |
| §4.4 scorer registry 4 tables + pg_notify | Q6 |
| §4.6 billing rekey, plan_entitlements | Q4 |
| §5 cutover sequence | Q3 |
| §6 ~150-line registry adapter as Phase-1 mandatory | Q6.b — **non-negotiable** |

---

## 10. Spec Self-Review Checklist

- [x] No "TBD" / "TODO" / placeholder requirements
- [x] No internal contradictions (e.g., halfvec column type matches HNSW operator class `halfvec_cosine_ops`)
- [x] Scope is one implementation plan, not multiple
- [x] Every requirement single-interpretation
- [x] Every section traces to a locked decision (§9)
- [x] Migration plan has rollback (§5)
- [x] What-this-looks-like-at-15-users-vs-10k explicit (§7)
- [x] Out-of-scope explicit (§8)

**Ready for `/writing-plans`.**
