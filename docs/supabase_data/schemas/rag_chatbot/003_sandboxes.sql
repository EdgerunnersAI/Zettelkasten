-- ============================================================================
-- 003_sandboxes.sql
-- Persistent NotebookLM-style curated Zettel collections.
-- No denormalized member_count (removed after scale review) — use rag_sandbox_stats view.
-- Composite FK (user_id, node_id) → kg_nodes CASCADE keeps membership consistent.
-- ============================================================================

CREATE TABLE IF NOT EXISTS rag_sandboxes (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID            NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    name            TEXT            NOT NULL,
    description     TEXT,
    icon            TEXT,                                         -- emoji or icon slug
    color           TEXT,                                         -- hex color for UI
    default_quality TEXT            NOT NULL DEFAULT 'fast'
                        CHECK (default_quality IN ('fast', 'high')),
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

    UNIQUE (user_id, name)
);

COMMENT ON TABLE rag_sandboxes IS 'Persistent NotebookLM-style curated Zettel collections';
COMMENT ON COLUMN rag_sandboxes.default_quality IS 'fast=gemini-flash default, high=gemini-pro or claude when enabled';

CREATE INDEX IF NOT EXISTS idx_rag_sandboxes_user
    ON rag_sandboxes (user_id, last_used_at DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS rag_sandbox_members (
    sandbox_id      UUID            NOT NULL REFERENCES rag_sandboxes(id) ON DELETE CASCADE,
    user_id         UUID            NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    node_id         TEXT            NOT NULL,
    added_via       TEXT            NOT NULL DEFAULT 'manual'
                        CHECK (added_via IN ('manual', 'bulk_tag', 'bulk_source', 'graph_pick', 'migration')),
    added_filter    JSONB,
    added_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),

    PRIMARY KEY (sandbox_id, node_id),
    FOREIGN KEY (user_id, node_id) REFERENCES kg_nodes(user_id, id) ON DELETE CASCADE
);

COMMENT ON TABLE  rag_sandbox_members IS 'Zettel membership per sandbox; cascade on kg_nodes delete';
COMMENT ON COLUMN rag_sandbox_members.added_via IS 'How the Zettel entered the sandbox — for UI grouping & bulk undo';

CREATE INDEX IF NOT EXISTS idx_rag_sandbox_members_sandbox
    ON rag_sandbox_members (sandbox_id);

CREATE INDEX IF NOT EXISTS idx_rag_sandbox_members_node
    ON rag_sandbox_members (user_id, node_id);

-- Stats view: computed on read instead of a denormalized column.
-- Users read sandbox lists rarely; bulk adds touch thousands of rows.
CREATE OR REPLACE VIEW rag_sandbox_stats AS
SELECT
    s.id, s.user_id, s.name, s.description, s.icon, s.color,
    s.default_quality, s.last_used_at, s.created_at, s.updated_at,
    (SELECT COUNT(*) FROM rag_sandbox_members m WHERE m.sandbox_id = s.id) AS member_count
FROM rag_sandboxes s;

COMMENT ON VIEW rag_sandbox_stats IS 'Sandbox metadata + dynamically computed member_count';

-- RLS
ALTER TABLE rag_sandboxes         ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_sandbox_members   ENABLE ROW LEVEL SECURITY;

-- Policies follow the same pattern as kg_nodes: SELECT/INSERT/UPDATE/DELETE per user
-- + service_role bypass. Full policy set:

DROP POLICY IF EXISTS rag_sandboxes_select ON rag_sandboxes;
CREATE POLICY rag_sandboxes_select ON rag_sandboxes
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = rag_sandboxes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS rag_sandboxes_insert ON rag_sandboxes;
CREATE POLICY rag_sandboxes_insert ON rag_sandboxes
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = rag_sandboxes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS rag_sandboxes_update ON rag_sandboxes;
CREATE POLICY rag_sandboxes_update ON rag_sandboxes
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = rag_sandboxes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS rag_sandboxes_delete ON rag_sandboxes;
CREATE POLICY rag_sandboxes_delete ON rag_sandboxes
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = rag_sandboxes.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS rag_sandboxes_service_all ON rag_sandboxes;
CREATE POLICY rag_sandboxes_service_all ON rag_sandboxes
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

-- Same policy set for rag_sandbox_members (select/insert/update/delete + service_role)
DROP POLICY IF EXISTS rag_sandbox_members_select ON rag_sandbox_members;
CREATE POLICY rag_sandbox_members_select ON rag_sandbox_members
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = rag_sandbox_members.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS rag_sandbox_members_insert ON rag_sandbox_members;
CREATE POLICY rag_sandbox_members_insert ON rag_sandbox_members
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = rag_sandbox_members.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS rag_sandbox_members_delete ON rag_sandbox_members;
CREATE POLICY rag_sandbox_members_delete ON rag_sandbox_members
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = rag_sandbox_members.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS rag_sandbox_members_service_all ON rag_sandbox_members;
CREATE POLICY rag_sandbox_members_service_all ON rag_sandbox_members
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

-- Rollback: DROP TABLE rag_sandbox_members, rag_sandboxes CASCADE;
--           DROP VIEW rag_sandbox_stats;
