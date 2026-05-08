-- Apply after the canonical chunk backfill completes.

SET maintenance_work_mem = '1GB';

CREATE INDEX IF NOT EXISTS idx_canonical_chunks_embedding_hnsw
    ON content.canonical_chunks
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

