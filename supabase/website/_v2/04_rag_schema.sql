-- DB v2 RAG schema: kastens, chat, retrieval weights, and scorer registry.

CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.kastens (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    name             text NOT NULL,
    description      text,
    icon             text,
    color            text,
    default_quality  text NOT NULL DEFAULT 'fast' CHECK (default_quality IN ('fast', 'high')),
    last_used_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS rag.kasten_members (
    kasten_id     uuid NOT NULL REFERENCES rag.kastens(id) ON DELETE CASCADE,
    workspace_id  uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    role          text NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    added_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kasten_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_kasten_members_workspace ON rag.kasten_members(workspace_id);

CREATE TABLE IF NOT EXISTS rag.kasten_zettels (
    kasten_id            uuid NOT NULL REFERENCES rag.kastens(id) ON DELETE CASCADE,
    workspace_zettel_id  uuid NOT NULL REFERENCES content.workspace_zettels(id) ON DELETE CASCADE,
    added_via            text NOT NULL CHECK (added_via IN ('manual', 'bulk_tag', 'bulk_source', 'graph_pick', 'migration')),
    added_filter         jsonb,
    added_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kasten_id, workspace_zettel_id)
);

CREATE TABLE IF NOT EXISTS rag.chat_sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    profile_id    uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    kasten_id     uuid REFERENCES rag.kastens(id) ON DELETE CASCADE,
    title         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag.chat_messages (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        uuid NOT NULL REFERENCES rag.chat_sessions(id) ON DELETE CASCADE,
    workspace_id      uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    role              text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content           text NOT NULL,
    citations         jsonb NOT NULL DEFAULT '[]'::jsonb,
    verdict           text CHECK (verdict IN ('supported', 'unsupported', 'retried_supported', 'partial')),
    retrieval_run_id  uuid,
    token_counts      jsonb,
    latency_ms        int,
    created_at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE rag.chat_messages
    DROP CONSTRAINT IF EXISTS chat_messages_citations_is_array;
ALTER TABLE rag.chat_messages
    ADD CONSTRAINT chat_messages_citations_is_array
    CHECK (jsonb_typeof(citations) = 'array');

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON rag.chat_messages(session_id, created_at);

CREATE OR REPLACE FUNCTION rag.assert_chat_message_workspace_match()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.workspace_id <> (SELECT workspace_id FROM rag.chat_sessions WHERE id = NEW.session_id) THEN
        RAISE EXCEPTION 'chat_messages.workspace_id must match chat_sessions.workspace_id';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_chat_messages_workspace_check ON rag.chat_messages;
CREATE TRIGGER trg_chat_messages_workspace_check
    BEFORE INSERT OR UPDATE ON rag.chat_messages
    FOR EACH ROW EXECUTE FUNCTION rag.assert_chat_message_workspace_match();

CREATE TABLE IF NOT EXISTS rag.retrieval_signal_weights (
    workspace_id                 uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    source_canonical_chunk_id    uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
    target_canonical_chunk_id    uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
    query_class                  text NOT NULL,
    weight                       double precision NOT NULL,
    refreshed_at                 timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, source_canonical_chunk_id, target_canonical_chunk_id, query_class)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_signal_workspace_target
    ON rag.retrieval_signal_weights(workspace_id, target_canonical_chunk_id);

CREATE TABLE IF NOT EXISTS rag.retrieval_scorer_registry (
    scorer_name       text PRIMARY KEY,
    impl_class        text NOT NULL,
    supported_inputs  jsonb NOT NULL DEFAULT '{}'::jsonb,
    description       text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag.retrieval_scorer_version (
    scorer_name  text NOT NULL REFERENCES rag.retrieval_scorer_registry(scorer_name) ON DELETE CASCADE,
    version_id   text NOT NULL,
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes        text,
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scorer_name, version_id)
);

CREATE TABLE IF NOT EXISTS rag.retrieval_pipeline_config (
    environment  text NOT NULL CHECK (environment IN ('prod', 'staging', 'dev')),
    scorer_name  text NOT NULL,
    version_id   text NOT NULL,
    enabled      boolean NOT NULL DEFAULT true,
    weight       numeric NOT NULL DEFAULT 1.0,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   text,
    PRIMARY KEY (environment, scorer_name),
    FOREIGN KEY (scorer_name, version_id)
        REFERENCES rag.retrieval_scorer_version(scorer_name, version_id)
);

CREATE TABLE IF NOT EXISTS rag.retrieval_pipeline_config_history (
    id           bigserial PRIMARY KEY,
    environment  text NOT NULL,
    scorer_name  text NOT NULL,
    version_id   text NOT NULL,
    enabled      boolean NOT NULL,
    weight       numeric NOT NULL,
    changed_at   timestamptz NOT NULL DEFAULT now(),
    changed_by   text,
    reason       text
);

CREATE OR REPLACE FUNCTION rag.notify_pipeline_config_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD IS NOT DISTINCT FROM NEW THEN
        RETURN NEW;
    END IF;
    PERFORM pg_notify('retrieval_pipeline_config_change', NEW.environment);
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_retrieval_pipeline_config_notify ON rag.retrieval_pipeline_config;
CREATE TRIGGER trg_retrieval_pipeline_config_notify
    AFTER INSERT OR UPDATE ON rag.retrieval_pipeline_config
    FOR EACH ROW EXECUTE FUNCTION rag.notify_pipeline_config_change();

CREATE OR REPLACE FUNCTION rag.assert_kasten_owner_can_grant()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.role <> 'owner' AND NOT EXISTS (
        SELECT 1
          FROM rag.kasten_members
         WHERE kasten_id = NEW.kasten_id
           AND core.jwt_has_workspace_role(workspace_id, ARRAY['owner'])
           AND role = 'owner'
    ) THEN
        IF NOT core.is_service_role() THEN
            RAISE EXCEPTION 'only kasten owners can grant memberships';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_kasten_members_grant_check ON rag.kasten_members;
CREATE TRIGGER trg_kasten_members_grant_check
    BEFORE INSERT OR UPDATE ON rag.kasten_members
    FOR EACH ROW EXECUTE FUNCTION rag.assert_kasten_owner_can_grant();
