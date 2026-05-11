-- ============================================================================
-- Migration: 2026-04-26_delete_test_node
-- Plan: docs/superpowers/plans/2026-04-26-rag-improvements-iter-01-02.md (UX-7)
--
-- Purges the leftover test artifact node `web-test-title` ("Test Title") from
-- the production KG. Surfaced by the iter-01 live Chrome MCP walkthrough
-- (docs/rag_eval/_kasten_build_walkthrough.md) where it appeared as a
-- legitimate-looking zettel in Naruto's recent /home feed.
--
-- Scope: single global delete by id. ON DELETE CASCADE on kg_links handles
-- any incident edges. Idempotent: re-running is a no-op once the row is gone.
--
-- Live-verify gate: deploy runner must apply this and confirm via
--   SELECT 1 FROM kg_nodes WHERE id = 'web-test-title';   -- expect 0 rows
-- ============================================================================

DELETE FROM kg_nodes WHERE id = 'web-test-title';

-- Defensive: also purge any chunks orphaned by the delete (in case the chunk
-- table FK is not declared with ON DELETE CASCADE in older deploys).
DELETE FROM kg_node_chunks WHERE node_id = 'web-test-title';
