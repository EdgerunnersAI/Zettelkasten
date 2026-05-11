-- ============================================================================
-- Migration: 2026-04-26_expand_subgraph
-- Plan: docs/superpowers/plans/2026-04-26-rag-improvements-iter-01-02.md (T18)
--
-- Adds the kg_expand_subgraph RPC: a recursive-CTE BFS over kg_links (in
-- both edge directions) that returns the deduped neighbourhood of a seed
-- set up to `p_depth` hops. Used by the RetrievalPlanner adapter (T19) to
-- expand a query's seed nodes before scoring.
--
-- SECURITY DEFINER + explicit user_id filter inside the CTE replicates the
-- existing kg_links RLS isolation while letting the recursive walk run in
-- a single round-trip (RLS-on-recursive-CTE has known performance cliffs
-- in Postgres < 16; pinning the user filter inside the function is the
-- standard workaround used elsewhere in this schema).
--
-- Idempotent: CREATE OR REPLACE.
-- ============================================================================

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
  'Recursive BFS over kg_links (both directions) returning the deduped neighbourhood of p_node_ids up to p_depth hops, scoped to p_user_id. Excludes seed nodes from the result.';

-- Live-verify gate: deploy runner must apply this and the matching pytest
-- (tests/unit/kg/test_expand_subgraph.py) is mock-only — covers Python
-- wrapper but NOT the SQL recursion. A live integration test should be
-- added pre-promotion if the eval scores diverge from local mock-based
-- expectations.
