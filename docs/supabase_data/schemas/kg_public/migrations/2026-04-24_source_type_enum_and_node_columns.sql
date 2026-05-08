-- ============================================================================
-- Migration: 2026-04-24_source_type_enum_and_node_columns
--
-- Schema drift fixes surfaced by newsletter iter-10 and the engine refactor:
--   1. kg_nodes.source_type CHECK constraint lacks 'newsletter'
--      (newsletters were being stored as 'substack' which breaks the UI badge).
--   2. kg_nodes has no first-class engine_version column (register scripts
--      previously nested this under metadata as a workaround; KGNodeCreate
--      already exposes it as a top-level field).
--   3. kg_nodes has no first-class extraction_confidence column (same story).
--
-- Idempotent: re-running this migration is safe.
-- Preserves existing column defaults and NOT NULL constraints.
-- ============================================================================

-- ── 1. Rebuild the source_type CHECK constraint to include 'newsletter' ─────
-- Postgres has no "ALTER CONSTRAINT ADD VALUE" — we drop and recreate. Guarded
-- by a DO block so re-applying is a no-op once 'newsletter' is in the list.

DO $$
DECLARE
    cur_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid)
      INTO cur_def
      FROM pg_constraint
     WHERE conrelid = 'public.kg_nodes'::regclass
       AND conname  = 'kg_nodes_source_type_check';

    IF cur_def IS NULL OR position('''newsletter''' IN cur_def) = 0 THEN
        ALTER TABLE public.kg_nodes
            DROP CONSTRAINT IF EXISTS kg_nodes_source_type_check;

        ALTER TABLE public.kg_nodes
            ADD CONSTRAINT kg_nodes_source_type_check
            CHECK (source_type IN (
                'youtube', 'reddit', 'github', 'twitter',
                'substack', 'newsletter', 'medium', 'web', 'generic'
            ));
    END IF;
END
$$;


-- ── 2. Add engine_version column ────────────────────────────────────────────

ALTER TABLE public.kg_nodes
    ADD COLUMN IF NOT EXISTS engine_version TEXT;

COMMENT ON COLUMN public.kg_nodes.engine_version
    IS 'Summarization-engine version tag (e.g. engine_v2_rc1).';


-- ── 3. Add extraction_confidence column ─────────────────────────────────────

ALTER TABLE public.kg_nodes
    ADD COLUMN IF NOT EXISTS extraction_confidence TEXT;

COMMENT ON COLUMN public.kg_nodes.extraction_confidence
    IS 'Confidence bucket produced by the extraction/schema-fallback stage.';


-- ── Done ────────────────────────────────────────────────────────────────────
