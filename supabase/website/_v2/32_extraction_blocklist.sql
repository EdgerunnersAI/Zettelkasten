-- Phase 8.0 H5: port public.kg_extraction_blocklist -> pipelines.extraction_blocklist.
-- Per-Kasten negative-resolution cache (DNS-style); mutable runtime state mutated
-- live by the entity_anchor resolver. Per Research P, persistence (not in-memory)
-- is required so blocked entities survive blue/green flips.
-- Column shape mirrors the v1 table (see kg_public/migrations/2026-05-07_kg_extraction_blocklist.sql);
-- sandbox_id is the v2 workspace UUID and is FK'd to core.workspaces for cascade-on-delete.

CREATE TABLE IF NOT EXISTS pipelines.extraction_blocklist (
    sandbox_id          uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    entity_text_norm    text NOT NULL,                    -- lower(trim(text))
    consecutive_misses  int  NOT NULL DEFAULT 0,
    last_seen_at        timestamptz NOT NULL DEFAULT now(),
    blocked_until       timestamptz,                       -- NULL = active block
    PRIMARY KEY (sandbox_id, entity_text_norm)
);

CREATE INDEX IF NOT EXISTS extraction_blocklist_active_idx
    ON pipelines.extraction_blocklist (sandbox_id, blocked_until);

ALTER TABLE pipelines.extraction_blocklist ENABLE ROW LEVEL SECURITY;

CREATE POLICY extraction_blocklist_service_all ON pipelines.extraction_blocklist
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON TABLE pipelines.extraction_blocklist TO authenticated, service_role;
GRANT INSERT, UPDATE, DELETE ON TABLE pipelines.extraction_blocklist TO service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
