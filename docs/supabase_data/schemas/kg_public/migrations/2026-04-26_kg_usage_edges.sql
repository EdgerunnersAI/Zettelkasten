-- ============================================================================
-- Migration: kg_usage_edges + kg_usage_edges_agg + recompute_runs
-- Plan: docs/superpowers/plans/2026-04-26-rag-improvements-iter-01-02.md (T21)
--
-- Tracks empirical co-citation / co-retrieval signals between KG nodes for
-- the graph-score adapter (T22 writes events here, T24 reads aggregated
-- weights). The materialized view applies a 30-day exponential time-decay
-- to the per-event delta and is refreshed by a scheduled recompute job
-- which records its execution metadata in recompute_runs.
-- ============================================================================

CREATE TABLE IF NOT EXISTS kg_usage_edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
  source_node_id text NOT NULL,
  target_node_id text NOT NULL,
  query_class text NOT NULL,
  verdict text NOT NULL CHECK (verdict IN ('supported','retried_supported')),
  delta float NOT NULL DEFAULT 1.0,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kg_usage_edges_user_target
  ON kg_usage_edges (user_id, target_node_id);
CREATE INDEX IF NOT EXISTS idx_kg_usage_edges_class
  ON kg_usage_edges (query_class);

ALTER TABLE kg_usage_edges ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_owns_usage_edge_select" ON kg_usage_edges;
CREATE POLICY "user_owns_usage_edge_select" ON kg_usage_edges
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM kg_users u
      WHERE u.id = kg_usage_edges.user_id
        AND u.render_user_id = (SELECT auth.uid())::text
    )
  );

DROP POLICY IF EXISTS "user_owns_usage_edge_insert" ON kg_usage_edges;
CREATE POLICY "user_owns_usage_edge_insert" ON kg_usage_edges
  FOR INSERT WITH CHECK (
    EXISTS (
      SELECT 1 FROM kg_users u
      WHERE u.id = kg_usage_edges.user_id
        AND u.render_user_id = (SELECT auth.uid())::text
    )
  );

CREATE MATERIALIZED VIEW IF NOT EXISTS kg_usage_edges_agg AS
  SELECT user_id, source_node_id, target_node_id, query_class,
         SUM(delta * exp(-EXTRACT(epoch FROM (now()-created_at))/2592000.0)) AS weight
  FROM kg_usage_edges
  GROUP BY user_id, source_node_id, target_node_id, query_class;

CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_edges_agg
  ON kg_usage_edges_agg (user_id, source_node_id, target_node_id, query_class);

CREATE TABLE IF NOT EXISTS recompute_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ran_at timestamptz DEFAULT now(),
  job_name text NOT NULL,
  rows_inserted int DEFAULT 0,
  rows_aggregated int DEFAULT 0,
  status text NOT NULL,
  error_message text
);

-- Refresh wrapper for the materialized view. SECURITY DEFINER lets the cron
-- runner's anon-or-service-role JWT trigger REFRESH without owning the MV.
-- Used by ops/scripts/recompute_usage_edges.py (T22) after each batch insert.
CREATE OR REPLACE FUNCTION kg_refresh_usage_edges_agg()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY kg_usage_edges_agg;
EXCEPTION WHEN feature_not_supported THEN
  -- CONCURRENTLY requires a unique index AND non-empty MV; fall back to plain refresh
  REFRESH MATERIALIZED VIEW kg_usage_edges_agg;
END;
$$;

COMMENT ON FUNCTION kg_refresh_usage_edges_agg() IS
  'Refresh kg_usage_edges_agg, preferring CONCURRENTLY (non-blocking) and falling back to plain REFRESH on first-run when the MV is empty.';
