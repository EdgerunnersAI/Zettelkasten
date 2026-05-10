-- Phase 7.1: drop the redundant retrieval signal index.
-- The new idx_retrieval_signal_workspace_class_target (workspace_id, query_class, target_canonical_chunk_id)
-- INCLUDE (source_canonical_chunk_id, weight) shipped in Phase 1.A is a strict superset.
DROP INDEX IF EXISTS rag.idx_retrieval_signal_workspace_target;
NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
