-- ============================================================================
-- Supabase Knowledge Graph Schema
-- Run this in the Supabase SQL Editor to create all tables, indexes, RLS
-- policies, and views for multi-user knowledge graph storage.
-- ============================================================================

-- ── Enable required extensions ───────────────────────────────────────────────

-- pgcrypto for gen_random_uuid() (usually enabled by default in Supabase)
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ── Table: kg_users ─────────────────────────────────────────────────────────
-- Maps external Render auth users to KG-local records.
-- No passwords or sessions — auth is handled entirely by Render.

CREATE TABLE IF NOT EXISTS kg_users (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    render_user_id  TEXT        UNIQUE NOT NULL,
    display_name    TEXT,
    email           TEXT,
    avatar_url      TEXT,
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  kg_users IS 'KG user records — maps Render auth IDs to local UUIDs';
COMMENT ON COLUMN kg_users.render_user_id IS 'External user ID from Render authentication';


-- ── Table: kg_nodes ─────────────────────────────────────────────────────────
-- Knowledge graph nodes. Composite PK (user_id, id) allows the same node
-- slug across different users.

CREATE TABLE IF NOT EXISTS kg_nodes (
    id              TEXT        NOT NULL,
    user_id         UUID        NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    source_type     TEXT        NOT NULL CHECK (source_type IN (
                        'youtube', 'reddit', 'github', 'twitter',
                        'substack', 'newsletter', 'medium', 'web', 'generic'
                    )),
    summary         TEXT,
    tags            TEXT[]      NOT NULL DEFAULT '{}',
    url             TEXT        NOT NULL,
    node_date       DATE,
    metadata        JSONB       NOT NULL DEFAULT '{}',
    engine_version          TEXT,
    extraction_confidence   TEXT,
    aliases         TEXT[]      NOT NULL DEFAULT '{}',
    summary_hash    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, id)
);

COMMENT ON TABLE  kg_nodes IS 'Knowledge graph nodes — one per captured URL per user';
COMMENT ON COLUMN kg_nodes.id IS 'Node slug, e.g. yt-attention, gh-transformers';
COMMENT ON COLUMN kg_nodes.source_type IS 'Content source: youtube, reddit, github, twitter, substack, newsletter, medium, web (legacy: generic)';
COMMENT ON COLUMN kg_nodes.tags IS 'Normalized tags for linking and filtering';
COMMENT ON COLUMN kg_nodes.metadata IS 'Extensible JSON for source-specific extras';


-- ── Type: kg_link_relation ──────────────────────────────────────────────────
-- Edge-type enum for graph-aware retrieval (iter-08 Phase 8). Distinct from
-- kg_links.relation TEXT which stores the shared-tag label.
DO $$ BEGIN
    CREATE TYPE kg_link_relation AS ENUM ('shared_tag', 'cites', 'mentions', 'co_occurs');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;


-- ── Table: kg_links ─────────────────────────────────────────────────────────
-- Edges between nodes, scoped to a single user's graph.

CREATE TABLE IF NOT EXISTS kg_links (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    source_node_id  TEXT        NOT NULL,
    target_node_id  TEXT        NOT NULL,
    relation        TEXT        NOT NULL,
    relation_type   kg_link_relation NOT NULL DEFAULT 'shared_tag',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Composite FKs ensure source/target belong to the same user
    FOREIGN KEY (user_id, source_node_id) REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, target_node_id) REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE,

    -- Prevent duplicate edges
    UNIQUE (user_id, source_node_id, target_node_id, relation)
);

-- Defensive ALTER for existing databases that pre-date the relation_type column.
ALTER TABLE kg_links
    ADD COLUMN IF NOT EXISTS relation_type kg_link_relation NOT NULL DEFAULT 'shared_tag';

COMMENT ON TABLE  kg_links IS 'Knowledge graph edges — shared-tag relationships between nodes';
COMMENT ON COLUMN kg_links.relation IS 'The shared tag that forms this connection';
COMMENT ON COLUMN kg_links.relation_type IS
    'Edge type for graph-aware retrieval. Default shared_tag for back-compat. '
    'Distinct from kg_links.relation which stores the shared-tag label.';


-- ── Indexes ─────────────────────────────────────────────────────────────────

-- Tag-based queries: "find nodes with tag X", "nodes matching any of [X, Y]"
CREATE INDEX IF NOT EXISTS idx_kg_nodes_tags
    ON kg_nodes USING GIN (tags);

-- Alias array containment lookups (rag_resolve_entity_anchors alias branch)
CREATE INDEX IF NOT EXISTS kg_nodes_aliases_gin
    ON kg_nodes USING GIN (aliases);

-- Fuzzy alias matching via pg_trgm
CREATE INDEX IF NOT EXISTS kg_nodes_aliases_trgm
    ON kg_nodes USING GIN (array_to_string(aliases, ' ') gin_trgm_ops);

-- Filter by source type per user
CREATE INDEX IF NOT EXISTS idx_kg_nodes_user_source
    ON kg_nodes (user_id, source_type);

-- Date-ordered listing per user
CREATE INDEX IF NOT EXISTS idx_kg_nodes_user_date
    ON kg_nodes (user_id, node_date DESC);

-- Created-at ordering fallback when node_date is null
CREATE INDEX IF NOT EXISTS idx_kg_nodes_user_created_at
    ON kg_nodes (user_id, created_at DESC);

-- URL lookup for dedup checks
CREATE INDEX IF NOT EXISTS idx_kg_nodes_user_url
    ON kg_nodes (user_id, url);

-- Edge lookups by source and target
CREATE INDEX IF NOT EXISTS idx_kg_links_user_source
    ON kg_links (user_id, source_node_id);

CREATE INDEX IF NOT EXISTS idx_kg_links_user_target
    ON kg_links (user_id, target_node_id);

-- Edge-type index for graph-aware retrieval (iter-08 Phase 8).
CREATE INDEX IF NOT EXISTS kg_links_relation_type_idx
    ON kg_links (relation_type);


-- ── Updated-at trigger ──────────────────────────────────────────────────────
-- Automatically set updated_at on row modification.

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_kg_users_updated_at ON kg_users;
CREATE TRIGGER trg_kg_users_updated_at
    BEFORE UPDATE ON kg_users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_kg_nodes_updated_at ON kg_nodes;
CREATE TRIGGER trg_kg_nodes_updated_at
    BEFORE UPDATE ON kg_nodes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ── Row-Level Security (RLS) ────────────────────────────────────────────────
-- Users can only access their own KG data.
-- Service role key bypasses RLS for server-side admin operations.

ALTER TABLE kg_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_links ENABLE ROW LEVEL SECURITY;

-- kg_users: users can read/update their own record
DROP POLICY IF EXISTS kg_users_select ON kg_users;
CREATE POLICY kg_users_select ON kg_users
    FOR SELECT USING (render_user_id = (SELECT auth.uid())::text);

DROP POLICY IF EXISTS kg_users_update ON kg_users;
CREATE POLICY kg_users_update ON kg_users
    FOR UPDATE USING (render_user_id = (SELECT auth.uid())::text);

-- kg_nodes: full CRUD on own nodes
DROP POLICY IF EXISTS kg_nodes_select ON kg_nodes;
CREATE POLICY kg_nodes_select ON kg_nodes
    FOR SELECT USING (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_nodes_insert ON kg_nodes;
CREATE POLICY kg_nodes_insert ON kg_nodes
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_nodes_update ON kg_nodes;
CREATE POLICY kg_nodes_update ON kg_nodes
    FOR UPDATE USING (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_nodes_delete ON kg_nodes;
CREATE POLICY kg_nodes_delete ON kg_nodes
    FOR DELETE USING (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_nodes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

-- kg_links: full CRUD on own links
DROP POLICY IF EXISTS kg_links_select ON kg_links;
CREATE POLICY kg_links_select ON kg_links
    FOR SELECT USING (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_links_insert ON kg_links;
CREATE POLICY kg_links_insert ON kg_links
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_links_update ON kg_links;
CREATE POLICY kg_links_update ON kg_links
    FOR UPDATE USING (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_links_delete ON kg_links;
CREATE POLICY kg_links_delete ON kg_links
    FOR DELETE USING (
        EXISTS (
            SELECT 1
            FROM kg_users u
            WHERE u.id = kg_links.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );


-- ── Service-role policies ───────────────────────────────────────────────────
-- The service role key (used by the Python backend) bypasses RLS by default.
-- These explicit policies allow the server to manage all data when using
-- the anon key with a custom JWT claim.

DROP POLICY IF EXISTS kg_users_service_all ON kg_users;
CREATE POLICY kg_users_service_all ON kg_users
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

DROP POLICY IF EXISTS kg_nodes_service_all ON kg_nodes;
CREATE POLICY kg_nodes_service_all ON kg_nodes
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

DROP POLICY IF EXISTS kg_links_service_all ON kg_links;
CREATE POLICY kg_links_service_all ON kg_links
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );


-- ── Views ───────────────────────────────────────────────────────────────────

-- Per-user graph stats (uses subqueries to avoid count-multiplication from JOINs)
CREATE OR REPLACE VIEW kg_user_stats AS
SELECT
    u.id                    AS user_id,
    u.render_user_id,
    u.display_name,
    (SELECT COUNT(*) FROM kg_nodes n WHERE n.user_id = u.id) AS node_count,
    (SELECT COUNT(*) FROM kg_links l WHERE l.user_id = u.id) AS link_count,
    COALESCE(
        (SELECT array_agg(DISTINCT unnested)
         FROM kg_nodes n2, unnest(n2.tags) AS unnested
         WHERE n2.user_id = u.id),
        '{}'
    )                       AS all_tags
FROM kg_users u;

COMMENT ON VIEW kg_user_stats IS 'Aggregated KG stats per user — node count, link count, all tags';


-- Per-user graph data in a frontend-compatible {nodes, links} format
CREATE OR REPLACE VIEW kg_graph_view AS
SELECT
    u.id AS user_id,
    jsonb_build_object(
        'nodes',
        COALESCE(
            (SELECT jsonb_agg(
                jsonb_build_object(
                    'id',      n.id,
                    'name',    n.name,
                    'group',   n.source_type,
                    'summary', n.summary,
                    'tags',    n.tags,
                    'url',     n.url,
                    'date',    COALESCE(n.node_date::text, '')
                )
            )
            FROM kg_nodes n
            WHERE n.user_id = u.id),
            '[]'::jsonb
        ),
        'links',
        COALESCE(
            (SELECT jsonb_agg(
                jsonb_build_object(
                    'source',   l.source_node_id,
                    'target',   l.target_node_id,
                    'relation', l.relation
                )
            )
            FROM kg_links l
            WHERE l.user_id = u.id),
            '[]'::jsonb
        )
    ) AS graph_data
FROM kg_users u;

COMMENT ON VIEW kg_graph_view IS 'Per-user graph data as JSONB {nodes, links} for frontend consumption';


-- ── Usage edges (RAG graph-score signal, plan iter-01 T21) ─────────────────
-- Empirical co-citation / co-retrieval signals between KG nodes. Producer:
-- T22 recompute_usage_edges.py inserts events here when a synthesis step
-- cites two nodes together. Consumer: T24 graph_score.py reads the decayed
-- aggregate weights from kg_usage_edges_agg.
-- Added by migration: 2026-04-26_kg_usage_edges.sql

CREATE TABLE IF NOT EXISTS kg_usage_edges (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid        NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    source_node_id  text        NOT NULL,
    target_node_id  text        NOT NULL,
    query_class     text        NOT NULL,
    verdict         text        NOT NULL CHECK (verdict IN ('supported','retried_supported')),
    delta           float       NOT NULL DEFAULT 1.0,
    created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kg_usage_edges_user_target
    ON kg_usage_edges (user_id, target_node_id);
CREATE INDEX IF NOT EXISTS idx_kg_usage_edges_class
    ON kg_usage_edges (query_class);

ALTER TABLE kg_usage_edges ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_owns_usage_edge_select ON kg_usage_edges;
CREATE POLICY user_owns_usage_edge_select ON kg_usage_edges
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = kg_usage_edges.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS user_owns_usage_edge_insert ON kg_usage_edges;
CREATE POLICY user_owns_usage_edge_insert ON kg_usage_edges
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = kg_usage_edges.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_usage_edges_service_all ON kg_usage_edges;
CREATE POLICY kg_usage_edges_service_all ON kg_usage_edges
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

-- 30-day exponential time-decay aggregate for the graph-score adapter
CREATE MATERIALIZED VIEW IF NOT EXISTS kg_usage_edges_agg AS
    SELECT user_id, source_node_id, target_node_id, query_class,
           SUM(delta * exp(-EXTRACT(epoch FROM (now()-created_at))/2592000.0)) AS weight
    FROM kg_usage_edges
    GROUP BY user_id, source_node_id, target_node_id, query_class;

CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_edges_agg
    ON kg_usage_edges_agg (user_id, source_node_id, target_node_id, query_class);

CREATE TABLE IF NOT EXISTS recompute_runs (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    ran_at           timestamptz DEFAULT now(),
    job_name         text        NOT NULL,
    rows_inserted    int         DEFAULT 0,
    rows_aggregated  int         DEFAULT 0,
    status           text        NOT NULL,
    error_message    text
);

-- Refresh wrapper for the materialized view. Used by the ops cron job
-- (T22 ops/scripts/recompute_usage_edges.py) to trigger the MV refresh
-- without requiring the runner to own the view.
CREATE OR REPLACE FUNCTION kg_refresh_usage_edges_agg()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY kg_usage_edges_agg;
EXCEPTION WHEN feature_not_supported THEN
    REFRESH MATERIALIZED VIEW kg_usage_edges_agg;
END;
$$;

COMMENT ON FUNCTION kg_refresh_usage_edges_agg() IS
    'Refresh kg_usage_edges_agg; prefers CONCURRENTLY, falls back to plain REFRESH for first run.';


-- ── KG subgraph expansion RPC (plan iter-01 T18) ───────────────────────────
-- Recursive-CTE BFS over kg_links (both directions). Used by the
-- RetrievalPlanner adapter (T19) to expand seed nodes before scoring.
-- Added by migration: 2026-04-26_expand_subgraph.sql

CREATE OR REPLACE FUNCTION kg_expand_subgraph(
    p_user_id uuid,
    p_node_ids text[],
    p_depth int DEFAULT 1
) RETURNS TABLE(id text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    WITH RECURSIVE walk AS (
        SELECT unnest(p_node_ids) AS id, 0 AS d
        UNION ALL
        SELECT
            CASE WHEN l.source_node_id = w.id THEN l.target_node_id
                 ELSE l.source_node_id
            END AS id,
            w.d + 1
        FROM kg_links l
        JOIN walk w
          ON l.source_node_id = w.id OR l.target_node_id = w.id
        WHERE w.d < p_depth AND l.user_id = p_user_id
    )
    SELECT DISTINCT id FROM walk WHERE id <> ALL(p_node_ids);
$$;

COMMENT ON FUNCTION kg_expand_subgraph(uuid, text[], int) IS
    'Recursive BFS over kg_links (both directions) returning the deduped neighbourhood of p_node_ids up to p_depth hops, scoped to p_user_id. Excludes seed nodes.';


-- ── Migration tracking (D-1) ────────────────────────────────────────────────
-- Used by ops/scripts/apply_migrations.py to track which files in
-- supabase/website/kg_public/migrations/ have been applied. The runner
-- self-bootstraps this table on a fresh DB; included here for completeness.
CREATE TABLE IF NOT EXISTS _migrations_applied (
    name             TEXT PRIMARY KEY,
    applied_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum         TEXT NOT NULL,
    applied_by       TEXT,
    -- iter-03 §1C.4 audit-trail columns: who/what triggered the apply.
    deploy_git_sha   TEXT,
    deploy_id        TEXT,
    deploy_actor     TEXT,
    runner_hostname  TEXT
);


-- ── Table: kg_kasten_node_freq (iter-04 anti-magnet prior) ─────────────────
-- Per-Kasten top-1 retrieval-hit counts. Hybrid retriever applies a
-- multiplicative damping factor 1/(1+log(1+freq)) to candidate scores
-- post-RRF fusion, so nodes that magnet across unrelated queries inside a
-- Kasten lose ranking. Cold-start: penalty suppressed until total hits in
-- a Kasten >= 50 (see compute_frequency_penalty in
-- website/features/rag_pipeline/retrieval/kasten_freq.py). Migration:
-- supabase/website/kg_public/migrations/2026-04-30_kasten_node_frequency.sql.
CREATE TABLE IF NOT EXISTS kg_kasten_node_freq (
    kasten_id     UUID         NOT NULL,
    node_id       TEXT         NOT NULL,
    hit_count     INTEGER      NOT NULL DEFAULT 0,
    last_hit_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (kasten_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_kasten_node_freq_kasten
    ON kg_kasten_node_freq(kasten_id);


-- ── RPC: rag_kasten_chunk_counts (iter-08 chunk-share anti-magnet) ─────────
-- Per-Kasten chunk count per member node. Consumer:
-- website/features/rag_pipeline/retrieval/chunk_share.py applies a
-- multiplicative damping factor 1/sqrt(chunk_count) to candidate RRF scores
-- post-fusion, replacing the dead kasten_freq prior (RES-2: floor=50 never
-- crossed). Migration:
-- supabase/website/kg_public/migrations/2026-05-03_rag_kasten_chunk_counts.sql.
CREATE OR REPLACE FUNCTION rag_kasten_chunk_counts(p_sandbox_id uuid)
RETURNS TABLE (node_id text, chunk_count int)
LANGUAGE sql STABLE AS $$
    SELECT m.node_id, count(c.id)::int AS chunk_count
    FROM rag_sandbox_members m
    LEFT JOIN kg_node_chunks c
      ON c.node_id = m.node_id
     AND c.user_id = m.user_id
    WHERE m.sandbox_id = p_sandbox_id
    GROUP BY m.node_id
$$;


-- ── RPC: rag_resolve_entity_anchors (iter-08 Phase 6 entity anchor) ─────────
-- Map entity-name list to canonical Kasten member node_ids via fuzzy title
-- (kg_nodes.name ILIKE %e%) or exact tag membership. Migration:
-- supabase/website/kg_public/migrations/2026-05-03_rag_entity_anchor.sql.
CREATE OR REPLACE FUNCTION rag_resolve_entity_anchors(p_sandbox_id uuid, p_entities text[])
RETURNS TABLE (node_id text)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT n.id AS node_id
    FROM rag_sandbox_members m
    JOIN kg_nodes n
      ON n.id = m.node_id
     AND n.user_id = m.user_id
    WHERE m.sandbox_id = p_sandbox_id
      AND EXISTS (
        SELECT 1 FROM unnest(p_entities) e
        WHERE n.name ILIKE '%' || e || '%' OR e = ANY(n.tags)
      )
$$;


-- ── RPC: rag_one_hop_neighbours (iter-08 Phase 6 entity anchor) ────────────
-- Return Kasten-scoped 1-hop neighbours of the given anchor node_ids over
-- kg_links (treated undirected). Used to widen the candidate set so author/
-- entity-anchored queries pull adjacent zettels for the entity-anchor boost.
CREATE OR REPLACE FUNCTION rag_one_hop_neighbours(p_sandbox_id uuid, p_anchor_nodes text[])
RETURNS TABLE (node_id text)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT nbr AS node_id
    FROM (
        SELECT CASE WHEN l.source_node_id = ANY(p_anchor_nodes)
                    THEN l.target_node_id
                    ELSE l.source_node_id END AS nbr
        FROM kg_links l
        JOIN rag_sandbox_members m_anchor
          ON (m_anchor.node_id = l.source_node_id AND m_anchor.user_id = l.user_id)
          OR (m_anchor.node_id = l.target_node_id AND m_anchor.user_id = l.user_id)
        WHERE m_anchor.sandbox_id = p_sandbox_id
          AND (l.source_node_id = ANY(p_anchor_nodes) OR l.target_node_id = ANY(p_anchor_nodes))
    ) edges
    JOIN rag_sandbox_members m_nbr
      ON m_nbr.node_id = edges.nbr
     AND m_nbr.sandbox_id = p_sandbox_id
$$;


-- iter-09 RES-7 / Q10: anchor-seed RPC. Returns seed candidates for the
-- supplied anchor node ids restricted to the sandbox's members. INNER JOIN
-- on rag_sandbox_members is mandatory for cross-tenant safety. Returns the
-- best chunk per node so callers can build a RetrievalCandidate without a
-- second roundtrip; cross-encoder rerank decides final ordering.
CREATE OR REPLACE FUNCTION rag_fetch_anchor_seeds(
    p_sandbox_id      uuid,
    p_anchor_nodes    text[],
    p_query_embedding vector(768)
) RETURNS TABLE (
    node_id     text,
    chunk_id    uuid,
    chunk_idx   int,
    kind        text,
    name        text,
    source_type text,
    url         text,
    content     text,
    tags        text[],
    score       double precision
)
LANGUAGE sql STABLE AS $$
    WITH ranked AS (
        SELECT
            m.node_id,
            kc.id                                              AS chunk_id,
            kc.chunk_idx,
            'chunk'::text                                      AS kind,
            COALESCE(n.name, m.node_id)                        AS name,
            COALESCE(n.source_type, 'web')                     AS source_type,
            COALESCE(n.url, '')                                AS url,
            COALESCE(kc.content, '')                           AS content,
            COALESCE(n.tags, ARRAY[]::text[])                  AS tags,
            1 - (kc.embedding <=> p_query_embedding)           AS score,
            ROW_NUMBER() OVER (
                PARTITION BY m.node_id
                ORDER BY kc.embedding <=> p_query_embedding ASC
            ) AS rn
        FROM rag_sandbox_members m
        INNER JOIN kg_node_chunks kc
          ON kc.node_id = m.node_id
         AND kc.user_id = m.user_id
        LEFT JOIN kg_nodes n
          ON n.id = m.node_id
         AND n.user_id = m.user_id
        WHERE m.sandbox_id = p_sandbox_id
          AND m.node_id = ANY(p_anchor_nodes)
    )
    SELECT node_id, chunk_id, chunk_idx, kind, name, source_type, url, content, tags, score
    FROM ranked
    WHERE rn = 1
    ORDER BY score DESC
    LIMIT 8;
$$;

GRANT EXECUTE ON FUNCTION rag_fetch_anchor_seeds(uuid, text[], vector) TO anon, authenticated;


-- ── RPC: rag_dense_recall (iter-10 P5 / Q6,Q7 dense-only fallback) ─────────
-- Last-resort recall when `rag_hybrid_search` returns zero rows for every
-- variant AND the kasten has members. Single dense pass scoped to all
-- effective_nodes so cross-encoder still has SOMETHING to rerank. Server-
-- gated by RAG_DENSE_FALLBACK_ENABLED + the empty-pool branch in
-- hybrid.retrieve. Mirrors supabase/website/kg_public/migrations/
-- 2026-05-05_rag_dense_recall.sql.
CREATE OR REPLACE FUNCTION rag_dense_recall(
    p_user_id         uuid,
    p_effective_nodes text[],
    p_query_embedding vector(768),
    p_limit           int DEFAULT 8
) RETURNS TABLE (
    kind        text,
    node_id     text,
    chunk_id    uuid,
    chunk_idx   int,
    name        text,
    source_type text,
    url         text,
    content     text,
    tags        text[],
    rrf_score   double precision
)
LANGUAGE sql STABLE AS $$
    SELECT
        'chunk'::text                                      AS kind,
        n.id                                               AS node_id,
        kc.id                                              AS chunk_id,
        kc.chunk_idx,
        COALESCE(n.name, n.id)                             AS name,
        COALESCE(n.source_type, 'web')                     AS source_type,
        COALESCE(n.url, '')                                AS url,
        COALESCE(kc.content, '')                           AS content,
        COALESCE(n.tags, ARRAY[]::text[])                  AS tags,
        1 - (kc.embedding <=> p_query_embedding)           AS rrf_score
    FROM kg_node_chunks kc
    JOIN kg_nodes n
      ON n.id = kc.node_id
     AND n.user_id = kc.user_id
    WHERE n.user_id = p_user_id
      AND n.id = ANY(p_effective_nodes)
    ORDER BY kc.embedding <=> p_query_embedding ASC
    LIMIT p_limit;
$$;

GRANT EXECUTE ON FUNCTION rag_dense_recall(uuid, text[], vector, int) TO anon, authenticated;


-- supabase/website/kg_public/migrations/2026-05-07_kasten_metrics.sql.
-- iter-12 Task 7 / Class K4 — per-Kasten rolling top-1 frequency record.
CREATE TABLE IF NOT EXISTS kg_kasten_metrics (
  id           bigserial    PRIMARY KEY,
  sandbox_id   uuid         NOT NULL,
  top1_node_id text         NOT NULL,
  ts           timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kg_kasten_metrics_sandbox_ts_idx
  ON kg_kasten_metrics(sandbox_id, ts DESC);


-- ── Done ────────────────────────────────────────────────────────────────────
-- Run this SQL in the Supabase SQL Editor (Dashboard → SQL Editor → New query).
-- After running, verify tables exist in Table Editor.
