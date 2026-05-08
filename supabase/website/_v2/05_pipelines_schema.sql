-- DB v2 pipeline-run schema.

CREATE SCHEMA IF NOT EXISTS pipelines;

CREATE TABLE IF NOT EXISTS pipelines.pipeline_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid REFERENCES core.workspaces(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (
        kind IN ('summarize', 'kg_extract', 'rag_ingest', 'nexus_ingest', 'metadata_enrich', 'retrieval_query', 'recompute_signals')
    ),
    status        text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    config        jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics       jsonb NOT NULL DEFAULT '{}'::jsonb,
    error         text,
    started_at    timestamptz,
    finished_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_workspace_kind
    ON pipelines.pipeline_runs(workspace_id, kind, created_at DESC);

CREATE TABLE IF NOT EXISTS pipelines.pipeline_run_items (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id               uuid NOT NULL REFERENCES pipelines.pipeline_runs(id) ON DELETE CASCADE,
    workspace_zettel_id  uuid REFERENCES content.workspace_zettels(id) ON DELETE SET NULL,
    status               text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    attempt              int NOT NULL DEFAULT 1,
    result               jsonb NOT NULL DEFAULT '{}'::jsonb,
    error                text,
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_items_run
    ON pipelines.pipeline_run_items(run_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'chat_messages_retrieval_run_id_fkey'
           AND conrelid = 'rag.chat_messages'::regclass
    ) THEN
        ALTER TABLE rag.chat_messages
            ADD CONSTRAINT chat_messages_retrieval_run_id_fkey
            FOREIGN KEY (retrieval_run_id)
            REFERENCES pipelines.pipeline_runs(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

