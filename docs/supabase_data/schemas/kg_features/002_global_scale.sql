-- ============================================================================
-- Global Scale Migration (002)
-- Adds: global graph RPC, pagination, global-optimized indexes, global read
-- policies, unified get_kg_graph function for smooth global/user switching
-- Depends on: kg_public/schema.sql + 001_intelligence.sql
-- ============================================================================


-- ── 1. Global-optimized indexes ───────────────────────────────────────────
-- Existing indexes are (user_id, ...) prefixed — unusable for global queries.
-- These cover the unfiltered paths.

-- Global date ordering (for global graph default sort)
CREATE INDEX IF NOT EXISTS idx_kg_nodes_date_global
    ON kg_nodes (node_date DESC NULLS LAST, created_at DESC);

-- Global source-type filter
CREATE INDEX IF NOT EXISTS idx_kg_nodes_source_type_global
    ON kg_nodes (source_type);

-- Global link endpoints (for link dedup in global view)
CREATE INDEX IF NOT EXISTS idx_kg_links_endpoints_global
    ON kg_links (source_node_id, target_node_id, relation);

-- Active-user filter used in every global query
CREATE INDEX IF NOT EXISTS idx_kg_users_active
    ON kg_users (id) WHERE is_active = true;

COMMENT ON INDEX idx_kg_nodes_date_global IS 'Global graph: date-ordered node listing without user_id prefix';
COMMENT ON INDEX idx_kg_links_endpoints_global IS 'Global link dedup by (source, target, relation)';


-- ── 2. RLS: global read policies ──────────────────────────────────────────
-- Authenticated users can READ all nodes/links (global KG view) but only
-- WRITE their own. The existing per-user SELECT policies restrict reads to
-- own data — we add broader read policies for the global view.
--
-- NOTE: RLS is OR-based — if ANY policy grants access, the row is visible.
-- Adding a "read all" policy alongside the existing per-user policy means
-- authenticated users see all rows on SELECT but are still restricted on
-- INSERT/UPDATE/DELETE by the per-user policies.

DROP POLICY IF EXISTS kg_nodes_global_read ON kg_nodes;
CREATE POLICY kg_nodes_global_read ON kg_nodes
    FOR SELECT
    TO authenticated
    USING (true);

DROP POLICY IF EXISTS kg_links_global_read ON kg_links;
CREATE POLICY kg_links_global_read ON kg_links
    FOR SELECT
    TO authenticated
    USING (true);

DROP POLICY IF EXISTS kg_users_global_read ON kg_users;
CREATE POLICY kg_users_global_read ON kg_users
    FOR SELECT
    TO authenticated
    USING (true);

COMMENT ON POLICY kg_nodes_global_read ON kg_nodes IS 'Authenticated users can read all nodes (global KG view)';
COMMENT ON POLICY kg_links_global_read ON kg_links IS 'Authenticated users can read all links (global KG view)';


-- ── 3. RPC: get_kg_graph (unified per-user / global) ─────────────────────
-- Single function handles both views:
--   p_user_id = UUID  -> per-user graph (node IDs unique within user)
--   p_user_id = NULL  -> global graph (nodes deduped by slug, links merged)
--
-- Global dedup strategy: same node slug across users = same content.
-- We keep the richest version (most tags) and merge all links. This creates
-- natural cross-user bridges — shared content becomes a hub node.
--
-- Pagination: p_limit/p_offset apply to nodes. Links are filtered to only
-- reference nodes in the current page (prevents dangling references).

CREATE OR REPLACE FUNCTION get_kg_graph(
    p_user_id  UUID    DEFAULT NULL,
    p_limit    INT     DEFAULT 5000,
    p_offset   INT     DEFAULT 0
)
RETURNS JSONB
LANGUAGE plpgsql STABLE
SECURITY DEFINER
SET search_path = 'public'
SET statement_timeout = '10s'
AS $$
DECLARE
    result JSONB;
BEGIN
    IF p_user_id IS NOT NULL THEN
        -- ── Per-user graph ─────────────────────────────────────────
        WITH page_nodes AS (
            SELECT n.id, n.name, n.source_type, n.summary, n.tags,
                   n.url, n.node_date
            FROM kg_nodes n
            WHERE n.user_id = p_user_id
            ORDER BY n.node_date DESC NULLS LAST, n.created_at DESC
            LIMIT p_limit OFFSET p_offset
        ),
        page_node_ids AS (
            SELECT id FROM page_nodes
        ),
        page_links AS (
            SELECT DISTINCT ON (l.source_node_id, l.target_node_id, l.relation)
                l.source_node_id, l.target_node_id, l.relation,
                l.weight, l.link_type, l.description
            FROM kg_links l
            WHERE l.user_id = p_user_id
              AND l.source_node_id IN (SELECT id FROM page_node_ids)
              AND l.target_node_id IN (SELECT id FROM page_node_ids)
            ORDER BY l.source_node_id, l.target_node_id, l.relation,
                     l.weight DESC NULLS LAST
        )
        SELECT jsonb_build_object(
            'nodes', COALESCE(
                (SELECT jsonb_agg(
                    jsonb_build_object(
                        'id',        pn.id,
                        'name',      pn.name,
                        'group',     pn.source_type,
                        'summary',   pn.summary,
                        'tags',      pn.tags,
                        'url',       pn.url,
                        'date',      COALESCE(pn.node_date::text, ''),
                        'node_date', COALESCE(pn.node_date::text, '')
                    )
                ) FROM page_nodes pn),
                '[]'::jsonb
            ),
            'links', COALESCE(
                (SELECT jsonb_agg(
                    jsonb_build_object(
                        'source',      pl.source_node_id,
                        'target',      pl.target_node_id,
                        'relation',    pl.relation,
                        'weight',      pl.weight,
                        'link_type',   pl.link_type,
                        'description', pl.description
                    )
                ) FROM page_links pl),
                '[]'::jsonb
            ),
            'total_nodes', (
                SELECT COUNT(*) FROM kg_nodes WHERE user_id = p_user_id
            )
        ) INTO result;

    ELSE
        -- ── Global graph ───────────────────────────────────────────
        -- Dedup nodes by slug (id): keep version with most tags.
        -- Merge links across users: dedup by (source, target, relation).
        -- Only include active users' data.
        WITH ranked_nodes AS (
            SELECT n.id, n.name, n.source_type, n.summary, n.tags,
                   n.url, n.node_date, u.display_name AS owner_name,
                   ROW_NUMBER() OVER (
                       PARTITION BY n.id
                       ORDER BY array_length(n.tags, 1) DESC NULLS LAST,
                                n.created_at ASC
                   ) AS rn
            FROM kg_nodes n
            JOIN kg_users u ON u.id = n.user_id AND u.is_active = true
        ),
        page_nodes AS (
            SELECT id, name, source_type, summary, tags, url,
                   node_date, owner_name
            FROM ranked_nodes
            WHERE rn = 1
            ORDER BY node_date DESC NULLS LAST
            LIMIT p_limit OFFSET p_offset
        ),
        page_node_ids AS (
            SELECT id FROM page_nodes
        ),
        -- Count how many users captured each node slug (contributor count)
        node_contributors AS (
            SELECT n.id, COUNT(DISTINCT n.user_id) AS contributor_count
            FROM kg_nodes n
            JOIN kg_users u ON u.id = n.user_id AND u.is_active = true
            WHERE n.id IN (SELECT id FROM page_node_ids)
            GROUP BY n.id
        ),
        -- Merge links across users, dedup by (source, target, relation)
        -- Keep the highest weight when duplicates exist
        global_links AS (
            SELECT DISTINCT ON (l.source_node_id, l.target_node_id, l.relation)
                l.source_node_id, l.target_node_id, l.relation,
                l.weight, l.link_type, l.description
            FROM kg_links l
            JOIN kg_users u ON u.id = l.user_id AND u.is_active = true
            WHERE l.source_node_id IN (SELECT id FROM page_node_ids)
              AND l.target_node_id IN (SELECT id FROM page_node_ids)
            ORDER BY l.source_node_id, l.target_node_id, l.relation,
                     l.weight DESC NULLS LAST
        )
        SELECT jsonb_build_object(
            'nodes', COALESCE(
                (SELECT jsonb_agg(
                    jsonb_build_object(
                        'id',           pn.id,
                        'name',         pn.name,
                        'group',        pn.source_type,
                        'summary',      pn.summary,
                        'tags',         pn.tags,
                        'url',          pn.url,
                        'date',         COALESCE(pn.node_date::text, ''),
                        'owner',        pn.owner_name,
                        'contributors', COALESCE(nc.contributor_count, 1)
                    )
                )
                FROM page_nodes pn
                LEFT JOIN node_contributors nc ON nc.id = pn.id),
                '[]'::jsonb
            ),
            'links', COALESCE(
                (SELECT jsonb_agg(
                    jsonb_build_object(
                        'source',      gl.source_node_id,
                        'target',      gl.target_node_id,
                        'relation',    gl.relation,
                        'weight',      gl.weight,
                        'link_type',   gl.link_type,
                        'description', gl.description
                    )
                ) FROM global_links gl),
                '[]'::jsonb
            ),
            'total_nodes', (
                SELECT COUNT(DISTINCT n.id)
                FROM kg_nodes n
                JOIN kg_users u ON u.id = n.user_id AND u.is_active = true
            )
        ) INTO result;
    END IF;

    RETURN result;
END;
$$;

COMMENT ON FUNCTION get_kg_graph IS
    'Unified graph fetch: per-user (p_user_id set) or global (p_user_id NULL). '
    'Global view deduplicates nodes by slug and merges links across users. '
    'Supports pagination via p_limit/p_offset with total_nodes count.';


-- ── 4. RPC: get_kg_stats (per-user and global) ───────────────────────────

CREATE OR REPLACE FUNCTION get_kg_stats(
    p_user_id  UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql STABLE
SECURITY DEFINER
SET search_path = 'public'
SET statement_timeout = '5s'
AS $$
DECLARE
    result JSONB;
BEGIN
    IF p_user_id IS NOT NULL THEN
        SELECT jsonb_build_object(
            'node_count', (SELECT COUNT(*) FROM kg_nodes WHERE user_id = p_user_id),
            'link_count', (SELECT COUNT(*) FROM kg_links WHERE user_id = p_user_id),
            'user_count', 1
        ) INTO result;
    ELSE
        SELECT jsonb_build_object(
            'node_count', (
                SELECT COUNT(DISTINCT id)
                FROM kg_nodes n JOIN kg_users u ON u.id = n.user_id AND u.is_active = true
            ),
            'link_count', (
                SELECT COUNT(DISTINCT (source_node_id, target_node_id, relation))
                FROM kg_links l JOIN kg_users u ON u.id = l.user_id AND u.is_active = true
            ),
            'user_count', (SELECT COUNT(*) FROM kg_users WHERE is_active = true),
            'raw_node_count', (
                SELECT COUNT(*)
                FROM kg_nodes n JOIN kg_users u ON u.id = n.user_id AND u.is_active = true
            )
        ) INTO result;
    END IF;

    RETURN result;
END;
$$;

COMMENT ON FUNCTION get_kg_stats IS 'Aggregate KG stats: per-user or global. Global includes unique + raw counts.';


-- ── 5. Permissions ────────────────────────────────────────────────────────

REVOKE ALL ON FUNCTION get_kg_graph  FROM PUBLIC;
REVOKE ALL ON FUNCTION get_kg_stats  FROM PUBLIC;

GRANT EXECUTE ON FUNCTION get_kg_graph  TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION get_kg_stats  TO authenticated, service_role;


-- ── Done ──────────────────────────────────────────────────────────────────
-- Run AFTER 001_intelligence.sql. Verify:
--   SELECT proname FROM pg_proc WHERE proname IN ('get_kg_graph', 'get_kg_stats');
--   SELECT * FROM get_kg_graph(NULL, 10, 0);  -- global, 10 nodes
--   SELECT * FROM get_kg_graph('user-uuid-here'::uuid, 100, 0);  -- per-user
