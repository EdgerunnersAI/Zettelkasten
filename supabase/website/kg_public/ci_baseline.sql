-- ============================================================================
-- CI-only baseline for kg_public forward migrations.
--
-- This is intentionally a pre-migration legacy shape, not the current
-- schema.sql snapshot. Migration CI applies this first, then replays every file
-- in supabase/website/kg_public/migrations/ to catch ordering and syntax
-- regressions without editing historical migrations that may already be
-- checksummed in production.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  CREATE ROLE anon;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  CREATE ROLE authenticated;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  CREATE ROLE service_role;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

CREATE SCHEMA IF NOT EXISTS auth;

CREATE OR REPLACE FUNCTION auth.uid()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
  SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION auth.role()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT NULLIF(current_setting('request.jwt.claim.role', true), '')
$$;

CREATE TABLE IF NOT EXISTS kg_users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  render_user_id text UNIQUE NOT NULL,
  display_name   text,
  email          text,
  avatar_url     text,
  is_active      boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kg_nodes (
  id          text NOT NULL,
  user_id     uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  name        text NOT NULL,
  source_type text NOT NULL,
  summary     text,
  tags        text[] NOT NULL DEFAULT '{}',
  url         text NOT NULL,
  node_date   date,
  metadata    jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id),
  CONSTRAINT kg_nodes_source_type_check CHECK (
    source_type IN (
      'youtube', 'reddit', 'github', 'twitter',
      'substack', 'medium', 'web', 'generic'
    )
  )
);

CREATE TABLE IF NOT EXISTS kg_links (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  source_node_id text NOT NULL,
  target_node_id text NOT NULL,
  relation       text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (user_id, source_node_id)
    REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, target_node_id)
    REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE,
  UNIQUE (user_id, source_node_id, target_node_id, relation)
);

CREATE TABLE IF NOT EXISTS kg_node_chunks (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  node_id      text NOT NULL,
  chunk_idx    int NOT NULL,
  content      text NOT NULL,
  content_hash bytea NOT NULL,
  chunk_type   text NOT NULL CHECK (
    chunk_type IN ('atomic', 'semantic', 'late', 'recursive')
  ),
  start_offset int,
  end_offset   int,
  token_count  int,
  embedding    vector(768),
  fts          tsvector,
  metadata     jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (user_id, node_id)
    REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE,
  UNIQUE (user_id, node_id, chunk_idx)
);

CREATE TABLE IF NOT EXISTS rag_sandboxes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  name            text NOT NULL,
  description     text,
  icon            text,
  color           text,
  default_quality text NOT NULL DEFAULT 'fast'
                    CHECK (default_quality IN ('fast', 'high')),
  last_used_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS rag_sandbox_members (
  sandbox_id   uuid NOT NULL REFERENCES rag_sandboxes(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  node_id      text NOT NULL,
  added_via    text NOT NULL DEFAULT 'manual' CHECK (
    added_via IN ('manual', 'bulk_tag', 'bulk_source', 'graph_pick', 'migration')
  ),
  added_filter jsonb,
  added_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (sandbox_id, node_id),
  FOREIGN KEY (user_id, node_id)
    REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  sandbox_id        uuid REFERENCES rag_sandboxes(id) ON DELETE CASCADE,
  title             text NOT NULL DEFAULT 'New conversation',
  last_scope_filter jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_mode      text NOT NULL DEFAULT 'fast'
                      CHECK (quality_mode IN ('fast', 'high')),
  message_count     int NOT NULL DEFAULT 0,
  last_message_at   timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id          uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  user_id             uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  role                text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content             text NOT NULL,
  retrieved_node_ids  text[] NOT NULL DEFAULT '{}',
  retrieved_chunk_ids uuid[] NOT NULL DEFAULT '{}',
  citations           jsonb NOT NULL DEFAULT '[]'::jsonb,
  llm_model           text,
  token_counts        jsonb NOT NULL DEFAULT '{}'::jsonb,
  latency_ms          int,
  trace_id            text,
  critic_verdict      text,
  critic_notes        text,
  query_class         text CHECK (
    query_class IN ('lookup', 'vague', 'multi_hop', 'thematic', 'step_back')
  ),
  rewritten_query     text,
  transform_variants  text[] NOT NULL DEFAULT '{}',
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chat_messages_critic_verdict_check CHECK (
    critic_verdict IS NULL OR critic_verdict IN (
      'supported',
      'partial',
      'unsupported',
      'retried_supported',
      'retried_still_bad'
    )
  )
);
