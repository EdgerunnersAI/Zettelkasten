-- ============================================================================
-- 001_hnsw_migration.sql
-- Replaces IVFFlat with HNSW on kg_nodes.embedding.
-- All 3 blueprints converge on HNSW (incremental updates, single-digit ms latency).
-- m=16, ef_construction=64 is BP3 baseline; ef_search=100 set per-session in RPCs.
-- Enables pgvector 0.8+ iterative scan for multi-tenant correctness under RLS filter.
-- ============================================================================

-- NOTE: Run each statement OUTSIDE a transaction. Supabase SQL editor: paste separately.
-- CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction block.

DROP INDEX CONCURRENTLY IF EXISTS public.idx_kg_nodes_embedding;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kg_nodes_embedding_hnsw
    ON public.kg_nodes
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- pgvector 0.8+ iterative scan: when HNSW top-K is filtered down by WHERE clause
-- (user_id = X, node_id = ANY(...)), keep scanning until the requested limit is
-- reached. Safe no-op on pgvector < 0.8.
ALTER DATABASE postgres SET hnsw.iterative_scan = 'strict_order';

COMMENT ON INDEX public.idx_kg_nodes_embedding_hnsw IS
    'HNSW cosine index (m=16, ef_cons=64). Set ef_search=100 per query session for high recall.';

-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_kg_nodes_embedding_hnsw;
--   CREATE INDEX idx_kg_nodes_embedding ON public.kg_nodes
--       USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
--   ALTER DATABASE postgres RESET hnsw.iterative_scan;
