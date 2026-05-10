-- Phase 8.5.B-2 — two derived materialized views over rag.retrieval_feedback_events.
--
-- CQRS pattern (Microsoft GraphRAG / Neo4j Bloom+GraphRAG / HippoRAG / Obsidian Smart
-- Connections all separate viz from rank): one events log → two scope-correct projections.
--
-- Scopes (locked spec, schema-audit-verified):
--   - kg.kg_edge_viz_weights         workspace-scope, all event types     → /api/graph
--   - rag.kasten_retrieval_edge_signals  Kasten-scope, retrieval events  → ranker boost
--
-- RLS note: Postgres MVs do not inherit RLS (PG <=17 limitation, Supabase #17790).
-- MV reads are scoped at query time by repository code joining workspace_members /
-- kasten_members. The events log itself IS RLS-gated (file 34); MVs are derived.

-- ----------------------------------------------------------------------------
-- 1) Workspace-scope viz weights (broad signal, /api/graph)
-- ----------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS kg.kg_edge_viz_weights;
CREATE MATERIALIZED VIEW kg.kg_edge_viz_weights AS
    SELECT
        workspace_id,
        source_node_id,
        target_node_id,
        COUNT(*)                                       AS event_count,
        SUM(weight_delta)                              AS viz_weight,
        MAX(created_at)                                AS last_touched_at
      FROM rag.retrieval_feedback_events
     WHERE event_type IN ('impression','click','dwell','cite','expand','follow_up')
       AND source_node_id IS NOT NULL
       AND target_node_id IS NOT NULL
     GROUP BY workspace_id, source_node_id, target_node_id;

-- Unique index required for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX kgevw_pk
    ON kg.kg_edge_viz_weights (workspace_id, source_node_id, target_node_id);

-- Workspace-scope read pattern (per-workspace iteration in /api/graph)
CREATE INDEX kgevw_workspace_weight_idx
    ON kg.kg_edge_viz_weights (workspace_id, viz_weight DESC);

GRANT SELECT ON kg.kg_edge_viz_weights TO authenticated, service_role;

-- ----------------------------------------------------------------------------
-- 2) Kasten-scope retrieval edge signals (narrow signal, RAG ranker boost)
-- ----------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS rag.kasten_retrieval_edge_signals;
CREATE MATERIALIZED VIEW rag.kasten_retrieval_edge_signals AS
    SELECT
        workspace_id,
        kasten_id,
        source_node_id,
        target_node_id,
        SUM(weight_delta) FILTER (WHERE event_type IN ('cite','accept'))   AS positive_signal,
        SUM(weight_delta) FILTER (WHERE event_type = 'reject')             AS negative_signal,
        COUNT(*)                                                            AS event_count,
        MAX(created_at)                                                     AS last_event_at
      FROM rag.retrieval_feedback_events
     WHERE kasten_id IS NOT NULL
       AND source_node_id IS NOT NULL
       AND target_node_id IS NOT NULL
     GROUP BY workspace_id, kasten_id, source_node_id, target_node_id;

CREATE UNIQUE INDEX kres_pk
    ON rag.kasten_retrieval_edge_signals
       (workspace_id, kasten_id, source_node_id, target_node_id);

-- Hot-path index: ranker LEFT JOINs on (kasten_id, source_node_id) to get all candidate boosts
CREATE INDEX kres_kasten_source_idx
    ON rag.kasten_retrieval_edge_signals (workspace_id, kasten_id, source_node_id);

GRANT SELECT ON rag.kasten_retrieval_edge_signals TO authenticated, service_role;

-- ----------------------------------------------------------------------------
-- 3) Refresh-tracking meta tables (R5-coherence test reads from these)
-- ----------------------------------------------------------------------------
-- Postgres doesn't expose MV last-refresh-time natively. We track it ourselves so
-- the freshness regression test (8.5.R5-coherence) can assert "MV is not stale".
CREATE TABLE IF NOT EXISTS kg.mv_refresh_log (
    mv_name      text PRIMARY KEY,
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    refresh_ms   integer
);

GRANT SELECT ON kg.mv_refresh_log TO authenticated, service_role;
GRANT INSERT, UPDATE ON kg.mv_refresh_log TO service_role;

-- Helper: refresh + log in one call (used by pg_cron schedules in file 36)
CREATE OR REPLACE FUNCTION kg.refresh_signal_mv(p_mv_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_started timestamptz := clock_timestamp();
    v_ms integer;
BEGIN
    IF p_mv_name = 'kg.kg_edge_viz_weights' THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY kg.kg_edge_viz_weights;
    ELSIF p_mv_name = 'rag.kasten_retrieval_edge_signals' THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY rag.kasten_retrieval_edge_signals;
    ELSE
        RAISE EXCEPTION 'unknown MV: %', p_mv_name USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_ms := EXTRACT(MILLISECOND FROM clock_timestamp() - v_started)::integer;
    INSERT INTO kg.mv_refresh_log (mv_name, refreshed_at, refresh_ms)
    VALUES (p_mv_name, clock_timestamp(), v_ms)
    ON CONFLICT (mv_name) DO UPDATE
        SET refreshed_at = EXCLUDED.refreshed_at,
            refresh_ms   = EXCLUDED.refresh_ms;
END;
$$;

REVOKE ALL ON FUNCTION kg.refresh_signal_mv(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.refresh_signal_mv(text) TO service_role;

NOTIFY pgrst, 'reload schema';
