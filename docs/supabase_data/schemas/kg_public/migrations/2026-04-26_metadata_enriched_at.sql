-- ============================================================================
-- 2026-04-26_metadata_enriched_at.sql
-- Adds the metadata_enriched_at sentinel column to kg_node_chunks so that the
-- ingest-side MetadataEnricher (website/features/rag_pipeline/ingest/
-- metadata_enricher.py) and the one-off backfill script
-- (ops/scripts/backfill_metadata.py) can mark a chunk as "metadata done" and
-- skip it on subsequent runs.
--
-- Note: the canonical kg_node_chunks DDL lives in
-- supabase/website/rag_chatbot/002_chunks_table.sql; the matching column has
-- been added to that file as the source-of-truth schema. This migration is the
-- forward-deploy diff for already-provisioned environments.
-- ============================================================================

ALTER TABLE kg_node_chunks
    ADD COLUMN IF NOT EXISTS metadata_enriched_at TIMESTAMPTZ;

-- Partial index keeps "what's still un-enriched?" scans cheap during backfill.
CREATE INDEX IF NOT EXISTS idx_kg_node_chunks_meta_enriched_pending
    ON kg_node_chunks (created_at)
    WHERE metadata_enriched_at IS NULL;

-- Rollback:
--   DROP INDEX IF EXISTS idx_kg_node_chunks_meta_enriched_pending;
--   ALTER TABLE kg_node_chunks DROP COLUMN IF EXISTS metadata_enriched_at;
