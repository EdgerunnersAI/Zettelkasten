-- ============================================================================
-- 004_chat_sessions.sql
-- Multi-turn chat persistence. sandbox_id is NULLABLE (NULL = "all Zettels" scope).
-- Keeps message_count + last_message_at maintained via per-row trigger (chat messages
-- are inserted one at a time, no bulk-insert hotspot).
-- ============================================================================

CREATE TABLE IF NOT EXISTS chat_sessions (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID         NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    sandbox_id        UUID                   REFERENCES rag_sandboxes(id) ON DELETE CASCADE,
    title             TEXT         NOT NULL DEFAULT 'New conversation',
    last_scope_filter JSONB        NOT NULL DEFAULT '{}'::jsonb,
    quality_mode      TEXT         NOT NULL DEFAULT 'fast'
                        CHECK (quality_mode IN ('fast', 'high')),
    message_count     INT          NOT NULL DEFAULT 0,
    last_message_at   TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  chat_sessions IS 'Persistent chat conversations; optionally scoped to a sandbox';
COMMENT ON COLUMN chat_sessions.sandbox_id IS 'NULL = ad-hoc all-Zettels scope; else the sandbox this conversation queries';

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
    ON chat_sessions (user_id, last_message_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_sandbox_recent
    ON chat_sessions (sandbox_id, last_message_at DESC NULLS LAST)
    WHERE sandbox_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS chat_messages (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID         NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id             UUID         NOT NULL REFERENCES kg_users(id) ON DELETE CASCADE,
    role                TEXT         NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content             TEXT         NOT NULL,

    -- Retrieval audit trail (assistant messages only)
    retrieved_node_ids  TEXT[]       NOT NULL DEFAULT '{}',
    retrieved_chunk_ids UUID[]       NOT NULL DEFAULT '{}',
    citations           JSONB        NOT NULL DEFAULT '[]'::jsonb,

    -- LLM metadata
    llm_model           TEXT,
    token_counts        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    latency_ms          INT,
    trace_id            TEXT,

    -- Hallucination critic outcome
    critic_verdict      TEXT         CHECK (critic_verdict IN (
                            'supported', 'partial', 'unsupported',
                            'retried_supported', 'retried_still_bad',
                            'retried_low_confidence'
                        )),
    critic_notes        TEXT,

    -- Query transformation audit
    query_class         TEXT         CHECK (query_class IN ('lookup','vague','multi_hop','thematic','step_back')),
    rewritten_query     TEXT,
    transform_variants  TEXT[]       NOT NULL DEFAULT '{}',

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  chat_messages IS 'Individual turns in a chat session with full retrieval/LLM audit trail';
COMMENT ON COLUMN chat_messages.critic_verdict IS 'Answer Critic outcome: supported=OK, unsupported=hallucination, retried_*=after multi-query retry';

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_messages_user
    ON chat_messages (user_id, created_at DESC);

-- Trigger to maintain chat_sessions.message_count + last_message_at
CREATE OR REPLACE FUNCTION chat_session_stats_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE chat_sessions
       SET message_count   = message_count + 1,
           last_message_at = NEW.created_at,
           updated_at      = now()
     WHERE id = NEW.session_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_chat_session_stats ON chat_messages;
CREATE TRIGGER trg_chat_session_stats
    AFTER INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION chat_session_stats_update();

-- RLS
ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chat_sessions_select ON chat_sessions;
CREATE POLICY chat_sessions_select ON chat_sessions
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_sessions.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_sessions_insert ON chat_sessions;
CREATE POLICY chat_sessions_insert ON chat_sessions
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_sessions.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_sessions_update ON chat_sessions;
CREATE POLICY chat_sessions_update ON chat_sessions
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_sessions.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_sessions_delete ON chat_sessions;
CREATE POLICY chat_sessions_delete ON chat_sessions
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_sessions.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_sessions_service_all ON chat_sessions;
CREATE POLICY chat_sessions_service_all ON chat_sessions
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

DROP POLICY IF EXISTS chat_messages_select ON chat_messages;
CREATE POLICY chat_messages_select ON chat_messages
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_messages.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_messages_insert ON chat_messages;
CREATE POLICY chat_messages_insert ON chat_messages
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_messages.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_messages_update ON chat_messages;
CREATE POLICY chat_messages_update ON chat_messages
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_messages.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_messages_delete ON chat_messages;
CREATE POLICY chat_messages_delete ON chat_messages
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM kg_users u
            WHERE u.id = chat_messages.user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS chat_messages_service_all ON chat_messages;
CREATE POLICY chat_messages_service_all ON chat_messages
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

-- Rollback: DROP TABLE chat_messages, chat_sessions CASCADE;
