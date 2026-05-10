-- Phase 8.5.R6 — forward-compat: typed core + JSONB extension columns.
--
-- Locked spec: docs/superpowers/plans/2026-05-10-phase-8.5-hardening-additions.md.
-- Operator emphasis: "any type of Zettel, OR any type of Kasten, ask any type
-- of question". This migration adds the schema-evolution seams that let new
-- content kinds / chunk modalities / KG node-and-edge types land WITHOUT
-- per-kind DDL.
--
-- All columns NULLABLE-default-NOT-NULL — fully backward-compatible. Existing
-- rows back-fill to defaults atomically; existing routes ignore the new cols.
-- jsonb_path_ops chosen over default jsonb_ops (~3× smaller, faster @> per
-- Crunchy Data + pganalyze 2024–25 guidance).

-- ---------------------------------------------------------------------------
-- 1) content.canonical_zettels: kind discriminator + version + attrs
-- ---------------------------------------------------------------------------
ALTER TABLE content.canonical_zettels
    ADD COLUMN IF NOT EXISTS kind        text     NOT NULL DEFAULT 'zettel',
    ADD COLUMN IF NOT EXISTS schema_ver  smallint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS attrs       jsonb    NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS cz_kind_source_idx
    ON content.canonical_zettels (kind, source_type);

CREATE INDEX IF NOT EXISTS cz_attrs_gin
    ON content.canonical_zettels USING gin (attrs jsonb_path_ops);

COMMENT ON COLUMN content.canonical_zettels.kind IS
    'Phase 8.5.R6: semantic content class (zettel/note/clip/quote/diagram/...). '
    'Distinct from source_type (ingestion provenance). Open-set; validate at app layer.';
COMMENT ON COLUMN content.canonical_zettels.schema_ver IS
    'Phase 8.5.R6: payload contract version for attrs jsonb. Bump when attrs '
    'shape changes within a kind so old rows can be lazily migrated.';
COMMENT ON COLUMN content.canonical_zettels.attrs IS
    'Phase 8.5.R6: forward-compat JSONB extension. Hot filters MUST be promoted '
    'to typed columns; never RLS-key on attrs->>X.';

-- ---------------------------------------------------------------------------
-- 2) content.canonical_chunks: modality + version + embedding_meta
-- ---------------------------------------------------------------------------
ALTER TABLE content.canonical_chunks
    ADD COLUMN IF NOT EXISTS modality       text     NOT NULL DEFAULT 'text',
    ADD COLUMN IF NOT EXISTS schema_ver     smallint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS embedding_meta jsonb    NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS cc_modality_meta_gin
    ON content.canonical_chunks USING gin (embedding_meta jsonb_path_ops);

COMMENT ON COLUMN content.canonical_chunks.modality IS
    'Phase 8.5.R6: text/image/audio/code/table/... When new modality lands, add '
    'rows with modality=NEW + parallel embedding_<modality> halfvec col + partial '
    'HNSW per modality (LlamaIndex MultiModalVectorStoreIndex pattern).';
COMMENT ON COLUMN content.canonical_chunks.embedding_meta IS
    'Phase 8.5.R6: embedder name, dim, model version. Free to evolve; never RLS-keyed.';

-- ---------------------------------------------------------------------------
-- 3) kg.kg_nodes / kg.kg_edges: attrs only (entity_type already text/extensible)
-- ---------------------------------------------------------------------------
ALTER TABLE kg.kg_nodes
    ADD COLUMN IF NOT EXISTS attrs jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE kg.kg_edges
    ADD COLUMN IF NOT EXISTS attrs jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS kn_attrs_gin
    ON kg.kg_nodes USING gin (attrs jsonb_path_ops);

COMMENT ON COLUMN kg.kg_nodes.attrs IS
    'Phase 8.5.R6: per-node typed payload. New entity_type values land via app-'
    'layer registry (not DB enum) so Neo4j 5.26+ "dynamic labels and types" '
    'pattern composes with our v2 schema.';

-- ---------------------------------------------------------------------------
-- 4) rag.kastens: kind discriminator + version + attrs
-- ---------------------------------------------------------------------------
-- Operator-explicit emphasis: "any type of Kasten". Skip GIN on rag.kastens.attrs
-- (Kasten count per workspace tiny; jsonb_path_ops only pays back at >1k rows
-- per workspace — we're 5–20 Kastens/user).
ALTER TABLE rag.kastens
    ADD COLUMN IF NOT EXISTS kind        text     NOT NULL DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS schema_ver  smallint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS attrs       jsonb    NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS kastens_kind_idx
    ON rag.kastens (workspace_id, kind);

COMMENT ON COLUMN rag.kastens.kind IS
    'Phase 8.5.R6: Kasten-class discriminator. "standard" today; "ephemeral", '
    '"shared-with-team", "archived" candidate kinds when product surfaces lands.';

NOTIFY pgrst, 'reload schema';
