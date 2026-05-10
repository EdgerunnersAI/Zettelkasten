-- Phase 8.5.B-1 — append-only events log; SoT for both viz + retrieval MVs.
--
-- Locked spec: docs/superpowers/plans/2026-05-10-phase-8.5-hardening-additions.md (Task 8.5.B).
-- Decision rationale (cross-session): mem-vault observation zrUWPShYIYieiXSXi1uzh-Ml.
--
-- Scope discipline:
--   - workspace_id is the RLS / ownership boundary (RLS policies key on it).
--   - kasten_id is the RAG retrieval boundary (Kasten-scope MV filters on it).
--   - user_id is nullable (anonymise on offboarding per 8.5.R2 GDPR-pseudonymisation pattern,
--     preserves aggregate signal in MVs while severing identity link).
--
-- Event_type vocabulary (locked Item 4): impression, click, dwell, cite, accept, reject,
-- copy, expand, follow_up, abandon. Adding a new type later = ALTER TABLE + 1 line per
-- projection's WHERE; no schema redesign.
--
-- propensity_weight stored at log time (1/p(seen at rank k)). Required for unbiased
-- counterfactual learning later; impossible to reconstruct retrospectively.

CREATE TABLE IF NOT EXISTS rag.retrieval_feedback_events (
    event_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id      uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    kasten_id         uuid REFERENCES rag.kastens(id) ON DELETE SET NULL,
    user_id           uuid,
    session_id        uuid REFERENCES rag.chat_sessions(id) ON DELETE SET NULL,
    message_id        uuid REFERENCES rag.chat_messages(id) ON DELETE SET NULL,
    source_node_id    uuid,
    target_node_id    uuid,
    chunk_id          uuid,
    event_type        text NOT NULL CHECK (event_type IN (
                          'impression','click','dwell','cite','accept','reject',
                          'copy','expand','follow_up','abandon'
                      )),
    rank_at_render    smallint,
    propensity_weight real,
    weight_delta      real NOT NULL DEFAULT 1.0,
    is_holdout        boolean NOT NULL DEFAULT false,
    attrs             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Indexes for the two MVs' GROUP BY plus the workspace-time browsing query
CREATE INDEX IF NOT EXISTS rfe_workspace_created_idx
    ON rag.retrieval_feedback_events (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS rfe_workspace_kasten_created_idx
    ON rag.retrieval_feedback_events (workspace_id, kasten_id, created_at DESC)
    WHERE kasten_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS rfe_workspace_st_idx
    ON rag.retrieval_feedback_events (workspace_id, source_node_id, target_node_id)
    WHERE source_node_id IS NOT NULL AND target_node_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS rfe_kasten_st_idx
    ON rag.retrieval_feedback_events (workspace_id, kasten_id, source_node_id, target_node_id)
    WHERE kasten_id IS NOT NULL AND source_node_id IS NOT NULL AND target_node_id IS NOT NULL;

-- jsonb_path_ops GIN for attrs containment queries (~3x smaller than default jsonb_ops)
CREATE INDEX IF NOT EXISTS rfe_attrs_gin
    ON rag.retrieval_feedback_events USING gin (attrs jsonb_path_ops);

-- RLS: read = workspace member; write = self-as-user gated through workspace membership.
-- Note: MVs derived from this table do NOT inherit RLS (PG <=17 limitation); MV reads
-- are scoped by repository code joining workspace_members at query time.
ALTER TABLE rag.retrieval_feedback_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rfe_member_read ON rag.retrieval_feedback_events;
CREATE POLICY rfe_member_read
    ON rag.retrieval_feedback_events
    FOR SELECT
    TO authenticated
    USING (
        core.is_service_role()
        OR workspace_id = ANY (core.jwt_workspace_ids())
    );

DROP POLICY IF EXISTS rfe_self_insert ON rag.retrieval_feedback_events;
CREATE POLICY rfe_self_insert
    ON rag.retrieval_feedback_events
    FOR INSERT
    TO authenticated
    WITH CHECK (
        core.is_service_role()
        OR (
            user_id = auth.uid()
            AND workspace_id = ANY (core.jwt_workspace_ids())
        )
    );

DROP POLICY IF EXISTS rfe_service_all ON rag.retrieval_feedback_events;
CREATE POLICY rfe_service_all
    ON rag.retrieval_feedback_events
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- No UPDATE/DELETE policies for authenticated: events log is append-only.
-- service_role can mutate (covered by rfe_service_all) for the offboarding anonymisation
-- path (set user_id = NULL via account_purge.purge_user_dependencies — see 8.5.R2).

GRANT SELECT, INSERT ON rag.retrieval_feedback_events TO authenticated;
GRANT ALL ON rag.retrieval_feedback_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE rag.retrieval_feedback_events_event_id_seq TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';
NOTIFY pgrst, 'reload config';
