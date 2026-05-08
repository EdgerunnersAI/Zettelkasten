-- DB v2 content schema: canonical content plus per-workspace overlays.

CREATE SCHEMA IF NOT EXISTS content;

CREATE TABLE IF NOT EXISTS content.embedding_model_versions (
    version_id     text PRIMARY KEY,
    dimensions     int NOT NULL,
    introduced_at  timestamptz NOT NULL DEFAULT now(),
    retired_at     timestamptz,
    is_default     boolean NOT NULL DEFAULT false
);

INSERT INTO content.embedding_model_versions (version_id, dimensions, is_default)
VALUES ('gemini-001-mrl-768', 768, true)
ON CONFLICT (version_id) DO UPDATE
SET dimensions = EXCLUDED.dimensions,
    is_default = EXCLUDED.is_default;

CREATE TABLE IF NOT EXISTS content.canonical_zettels (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    normalized_url    text NOT NULL,
    content_hash      bytea NOT NULL,
    source_type       text NOT NULL CHECK (
        source_type IN ('youtube', 'reddit', 'github', 'twitter', 'substack', 'newsletter', 'medium', 'web', 'generic')
    ),
    title             text,
    body_md           text,
    publication_date  date,
    source_metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (normalized_url, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_canonical_zettels_hash
    ON content.canonical_zettels(content_hash);

CREATE TABLE IF NOT EXISTS content.canonical_chunks (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_zettel_id      uuid NOT NULL REFERENCES content.canonical_zettels(id) ON DELETE CASCADE,
    chunk_idx                int NOT NULL,
    content                  text NOT NULL,
    content_hash             bytea NOT NULL,
    chunk_type               text NOT NULL CHECK (chunk_type IN ('atomic', 'semantic', 'late', 'recursive')),
    start_offset             int,
    end_offset               int,
    token_count              int,
    embedding                halfvec(768),
    embedding_model_version  text NOT NULL DEFAULT 'gemini-001-mrl-768'
                             REFERENCES content.embedding_model_versions(version_id),
    fts                      tsvector,
    metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (canonical_zettel_id, chunk_idx)
);

CREATE INDEX IF NOT EXISTS idx_canonical_chunks_fts
    ON content.canonical_chunks USING gin (fts);
CREATE INDEX IF NOT EXISTS idx_canonical_chunks_zettel
    ON content.canonical_chunks(canonical_zettel_id);

CREATE OR REPLACE FUNCTION content.canonical_chunks_fts_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.fts := to_tsvector('english', COALESCE(NEW.content, ''));
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_canonical_chunks_fts ON content.canonical_chunks;
CREATE TRIGGER trg_canonical_chunks_fts
    BEFORE INSERT OR UPDATE OF content ON content.canonical_chunks
    FOR EACH ROW EXECUTE FUNCTION content.canonical_chunks_fts_update();

CREATE TABLE IF NOT EXISTS content.workspace_zettels (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id               uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    canonical_zettel_id        uuid NOT NULL REFERENCES content.canonical_zettels(id) ON DELETE RESTRICT,
    ai_summary                 text,
    ai_summary_engine_version  text,
    user_tags                  text[] NOT NULL DEFAULT '{}',
    user_note                  text,
    pinned                     boolean NOT NULL DEFAULT false,
    added_via                  text NOT NULL CHECK (added_via IN ('telegram', 'website', 'share', 'migration')),
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now(),
    deleted_at                 timestamptz,
    UNIQUE (workspace_id, canonical_zettel_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_zettels_workspace_tags
    ON content.workspace_zettels USING gin (user_tags);
CREATE INDEX IF NOT EXISTS idx_workspace_zettels_workspace_created
    ON content.workspace_zettels (workspace_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS content.workspace_chunk_membership (
    workspace_id          uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    canonical_chunk_id    uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
    workspace_zettel_id   uuid NOT NULL REFERENCES content.workspace_zettels(id) ON DELETE CASCADE,
    hit_count             int NOT NULL DEFAULT 0,
    last_hit_at           timestamptz,
    PRIMARY KEY (workspace_id, canonical_chunk_id, workspace_zettel_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_chunks_workspace
    ON content.workspace_chunk_membership(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_chunks_chunk
    ON content.workspace_chunk_membership(canonical_chunk_id);

CREATE OR REPLACE FUNCTION content.enqueue_canonical_shred_if_orphan(p_canonical_zettel_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM content.workspace_zettels wz
         WHERE wz.canonical_zettel_id = p_canonical_zettel_id
           AND wz.deleted_at IS NULL
    ) THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM rag.chat_messages cm,
               jsonb_array_elements(cm.citations) c
         WHERE (c ->> 'canonical_chunk_id')::uuid IN (
             SELECT id
               FROM content.canonical_chunks
              WHERE canonical_zettel_id = p_canonical_zettel_id
         )
    ) THEN
        RETURN;
    END IF;

    INSERT INTO core.soft_delete_queue (table_name, row_id, shred_after)
    VALUES ('content.canonical_zettels', p_canonical_zettel_id, now() + interval '7 days')
    ON CONFLICT DO NOTHING;
END
$$;

CREATE OR REPLACE FUNCTION content.trg_orphan_check_after_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM content.enqueue_canonical_shred_if_orphan(OLD.canonical_zettel_id);
    RETURN OLD;
END
$$;

CREATE OR REPLACE FUNCTION content.trg_orphan_check_after_softdelete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
        PERFORM content.enqueue_canonical_shred_if_orphan(NEW.canonical_zettel_id);
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_workspace_zettel_after_delete ON content.workspace_zettels;
CREATE TRIGGER trg_workspace_zettel_after_delete
    AFTER DELETE ON content.workspace_zettels
    FOR EACH ROW EXECUTE FUNCTION content.trg_orphan_check_after_delete();

DROP TRIGGER IF EXISTS trg_workspace_zettel_after_softdelete ON content.workspace_zettels;
CREATE TRIGGER trg_workspace_zettel_after_softdelete
    AFTER UPDATE OF deleted_at ON content.workspace_zettels
    FOR EACH ROW EXECUTE FUNCTION content.trg_orphan_check_after_softdelete();

CREATE OR REPLACE FUNCTION content.search_chunks(
    p_workspace_id uuid,
    p_query_embedding halfvec(768),
    p_limit int DEFAULT 32
) RETURNS TABLE (
    chunk_id uuid,
    canonical_zettel_id uuid,
    content text,
    score double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;

    PERFORM set_config('hnsw.iterative_scan', 'relaxed_order', true);

    RETURN QUERY
        SELECT cc.id,
               cc.canonical_zettel_id,
               cc.content,
               (1 - (cc.embedding <=> p_query_embedding))::double precision AS score
          FROM content.workspace_chunk_membership wcm
          JOIN content.canonical_chunks cc ON cc.id = wcm.canonical_chunk_id
         WHERE wcm.workspace_id = p_workspace_id
         ORDER BY cc.embedding <=> p_query_embedding
         LIMIT p_limit;
END
$$;

GRANT EXECUTE ON FUNCTION content.search_chunks(uuid, halfvec, int) TO authenticated;

