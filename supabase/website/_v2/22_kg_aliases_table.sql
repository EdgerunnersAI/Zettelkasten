-- Phase 1.D.4a: kg_node_aliases table for entity alias resolution.
-- Per Q1.D.4 approval (OpenAlex author disambiguation pattern, GraphRAG #847/#1718
-- anti-pattern avoidance, pg_trgm GIN for short-string fuzzy match).
-- Aliases populated at ingest from extractor output; never query-time merge.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS kg.kg_node_aliases (
    id           bigserial PRIMARY KEY,
    kg_node_id   bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
    alias        text   NOT NULL,
    alias_kind   text   NOT NULL DEFAULT 'surface_form' CHECK (
        alias_kind IN ('surface_form', 'synonym', 'abbreviation', 'translit', 'extractor_canonical')
    ),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kg_node_id, alias, alias_kind)
);

-- pg_trgm GIN for fuzzy ILIKE / similarity matching (Q1.D.4 — documented Postgres path)
CREATE INDEX IF NOT EXISTS idx_kg_node_aliases_alias_trgm
    ON kg.kg_node_aliases USING gin (alias gin_trgm_ops);

-- Reverse lookup btree
CREATE INDEX IF NOT EXISTS idx_kg_node_aliases_kg_node_id
    ON kg.kg_node_aliases (kg_node_id);

ALTER TABLE kg.kg_node_aliases ENABLE ROW LEVEL SECURITY;

-- Aliases inherit workspace scope from the parent kg_nodes row.
DROP POLICY IF EXISTS kg_node_aliases_select ON kg.kg_node_aliases;
CREATE POLICY kg_node_aliases_select ON kg.kg_node_aliases
  FOR SELECT USING (EXISTS (
      SELECT 1 FROM kg.kg_nodes n
      WHERE n.id = kg_node_aliases.kg_node_id
        AND n.workspace_id = ANY (core.jwt_workspace_ids())
  ));

DROP POLICY IF EXISTS kg_node_aliases_service_all ON kg.kg_node_aliases;
CREATE POLICY kg_node_aliases_service_all ON kg.kg_node_aliases
  FOR ALL
  USING ((SELECT current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role')
  WITH CHECK ((SELECT current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role');

-- Required table-level GRANTs (Round-2 R2.1.1 standard practice)
GRANT SELECT ON TABLE kg.kg_node_aliases TO authenticated, service_role;
GRANT INSERT, UPDATE, DELETE ON TABLE kg.kg_node_aliases TO service_role;
GRANT USAGE, SELECT ON SEQUENCE kg.kg_node_aliases_id_seq TO service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
