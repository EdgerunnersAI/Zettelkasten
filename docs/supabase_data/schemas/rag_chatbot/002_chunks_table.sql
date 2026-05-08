-- ============================================================================
-- 002_chunks_table.sql
-- Fine-grained chunks for long-form content; atomic single chunks for short-form.
-- Schema B: chunks for all new captures (user Q1). Existing Zettels stay summary-only
-- until a future backfill job (§12). Retrieval unions summary + chunk layers.
-- ============================================================================

CREATE TABLE IF NOT EXISTS kg_node_chunks (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID            NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    node_id         TEXT            NOT NULL,
    chunk_idx       INT             NOT NULL,                   -- 0-based order within the node
    content         TEXT            NOT NULL,
    content_hash    BYTEA           NOT NULL,                   -- sha256 binary (32 bytes)
    chunk_type      TEXT            NOT NULL CHECK (chunk_type IN (
                        'atomic', 'semantic', 'late', 'recursive'
                    )),
    start_offset    INT,
    end_offset      INT,
    token_count     INT,
    embedding       vector(768),                                 -- Gemini-001 via MRL
    fts             tsvector,                                    -- trigger-maintained
    metadata        JSONB           NOT NULL DEFAULT '{}',
    metadata_enriched_at TIMESTAMPTZ,                            -- set by ingest-side MetadataEnricher / backfill_metadata.py
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

    FOREIGN KEY (user_id, node_id) REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE,
    UNIQUE (user_id, node_id, chunk_idx)
);

COMMENT ON TABLE kg_node_chunks IS 'Fine-grained chunks for long-form Zettels + atomic single-chunk rows for short-form';
COMMENT ON COLUMN kg_node_chunks.chunk_type IS 'atomic=short-form, semantic=topic-boundary, late=context-preserving, recursive=fallback';
COMMENT ON COLUMN kg_node_chunks.metadata IS 'chunk-specific JSONB: youtube_timestamp, entities, parent_title, etc.';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_kg_node_chunks_user
    ON kg_node_chunks (user_id);

CREATE INDEX IF NOT EXISTS idx_kg_node_chunks_node
    ON kg_node_chunks (user_id, node_id);

CREATE INDEX IF NOT EXISTS idx_kg_node_chunks_embedding_hnsw
    ON kg_node_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_kg_node_chunks_fts
    ON kg_node_chunks USING GIN (fts);

CREATE INDEX IF NOT EXISTS idx_kg_node_chunks_hash
    ON kg_node_chunks (user_id, node_id, content_hash);

-- Partial index for cheap "what's still un-enriched?" scans during backfill.
CREATE INDEX IF NOT EXISTS idx_kg_node_chunks_meta_enriched_pending
    ON kg_node_chunks (created_at)
    WHERE metadata_enriched_at IS NULL;

-- FTS trigger: weight chunk content as the canonical text body
CREATE OR REPLACE FUNCTION kg_node_chunks_fts_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.fts := to_tsvector('english', coalesce(NEW.content, ''));
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_kg_node_chunks_fts ON kg_node_chunks;
CREATE TRIGGER trg_kg_node_chunks_fts
    BEFORE INSERT OR UPDATE OF content
    ON kg_node_chunks
    FOR EACH ROW EXECUTE FUNCTION kg_node_chunks_fts_update();

-- RLS mirrors kg_nodes patterns
ALTER TABLE kg_node_chunks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS kg_node_chunks_select ON kg_node_chunks;
CREATE POLICY kg_node_chunks_select ON kg_node_chunks
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = kg_node_chunks.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_node_chunks_insert ON kg_node_chunks;
CREATE POLICY kg_node_chunks_insert ON kg_node_chunks
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = kg_node_chunks.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_node_chunks_update ON kg_node_chunks;
CREATE POLICY kg_node_chunks_update ON kg_node_chunks
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = kg_node_chunks.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_node_chunks_delete ON kg_node_chunks;
CREATE POLICY kg_node_chunks_delete ON kg_node_chunks
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = kg_node_chunks.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS kg_node_chunks_service_all ON kg_node_chunks;
CREATE POLICY kg_node_chunks_service_all ON kg_node_chunks
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

-- Rollback: DROP TABLE kg_node_chunks CASCADE;
