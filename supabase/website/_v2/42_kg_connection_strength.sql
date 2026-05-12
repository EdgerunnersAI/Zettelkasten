-- WAVE-C 1c-A.1 — Multi-signal KG edge connection strength
--
-- Adds:
--   - kg.kg_edges.connection_strength NUMERIC(4,3) NULL  (range [0,1])
--     Composite score per locked decision D-KG-1:
--       embedding=0.55 + tag=0.25 + structural=0.15 + temporal=0.05
--   - Index on (workspace_key, connection_strength DESC) for the
--     min_strength filter buckets used by the per-user /api/graph cache
--     (D-KG-3: 0.7 strong, 0.4 medium, <0.4 weak).
--   - Backfill: existing rows default to 0.5 (mid-bucket) so the
--     per-user filter still returns predictable results until the
--     scorer pass runs over historical edges.
--
-- DEVIATION FROM TASK SPEC:
--   The task spec referenced `kg.edges` and `kg.embeddings`. The actual v2
--   schema (03_kg_schema.sql) uses `kg.kg_edges` and `kg.kg_nodes`; KG node
--   embeddings are NOT stored on a kg.embeddings table — embeddings live on
--   content.canonical_chunks (`embedding halfvec(768)`,
--   `embedding_model_version text NOT NULL DEFAULT 'gemini-001-mrl-768'`,
--   already FK'd to content.embedding_model_versions). No additional
--   embedding-side columns are added by this migration; the scorer reads
--   embeddings via the existing chunk surface.
--
-- Anti-pattern guards:
--   * Does NOT modify any existing function body (golden md5 protected).
--   * Forward-only ADD COLUMN IF NOT EXISTS; safe to apply repeatedly.
--   * No CREATE OR REPLACE on existing functions.

BEGIN;

ALTER TABLE kg.kg_edges
    ADD COLUMN IF NOT EXISTS connection_strength NUMERIC(4, 3);

-- Range guard: NULL allowed (legacy / awaiting scorer); when present, in [0, 1].
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'kg_edges_connection_strength_range'
           AND conrelid = 'kg.kg_edges'::regclass
    ) THEN
        ALTER TABLE kg.kg_edges
            ADD CONSTRAINT kg_edges_connection_strength_range
            CHECK (connection_strength IS NULL
                   OR (connection_strength >= 0 AND connection_strength <= 1));
    END IF;
END
$$;

-- Backfill legacy rows so min_strength bucket queries are deterministic.
-- 0.5 is the medium-bucket midpoint; the scorer pass overwrites it.
UPDATE kg.kg_edges
   SET connection_strength = 0.500
 WHERE connection_strength IS NULL;

-- Filter index for D-KG-3 bucket queries:
--   ?min_strength>=0.7 (strong) | 0.4-0.7 (medium) | <0.4 (weak)
-- The per-user /api/graph cache key includes the bucket label.
CREATE INDEX IF NOT EXISTS idx_kg_edges_workspace_strength
    ON kg.kg_edges (workspace_key, connection_strength DESC NULLS LAST);

COMMIT;
