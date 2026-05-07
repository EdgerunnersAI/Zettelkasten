-- iter-12 Task 28 R5: ingest-time entity canonicalization via LLM-generated aliases.
--
-- Adds two new columns to kg_nodes:
--   aliases       text[]  — LLM-generated alternative names for the node entity
--   summary_hash  text    — SHA-256 prefix of the summary used to generate the aliases
--                           (used to skip re-generation when summary is unchanged)
--
-- Adds a GIN index on aliases for fast array-contains lookups and a trigram
-- index on the generated aliases_flat column for fuzzy matching.
--
-- Replaces rag_resolve_entity_anchors to also match against node aliases and
-- emit a matched_via column so callers can see whether the match was on the
-- canonical name, tags, alias, or trgm fuzzy match.
--
-- Safe to re-run (all DDL is IF NOT EXISTS or CREATE OR REPLACE).

-- iter-12 Task 28 hotfix (2026-05-07): ensure pg_trgm is available before the
-- GIN trgm index is created.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE kg_nodes ADD COLUMN IF NOT EXISTS aliases text[] NOT NULL DEFAULT '{}';
ALTER TABLE kg_nodes ADD COLUMN IF NOT EXISTS summary_hash text;

CREATE INDEX IF NOT EXISTS kg_nodes_aliases_gin
    ON kg_nodes USING GIN (aliases);

-- iter-12 Task 28 hotfix (2026-05-07): IMMUTABLE wrapper + generated column.
-- array_to_string is STABLE → cannot be used in index expressions directly.
-- Wrapper function is provably IMMUTABLE for pure-text input (no locale paths).
CREATE OR REPLACE FUNCTION immutable_array_to_text(text[])
  RETURNS text
  LANGUAGE sql
  IMMUTABLE
  PARALLEL SAFE
  AS $$ SELECT array_to_string($1, ' ') $$;

ALTER TABLE kg_nodes
  ADD COLUMN IF NOT EXISTS aliases_flat text
  GENERATED ALWAYS AS (immutable_array_to_text(aliases)) STORED;

CREATE INDEX IF NOT EXISTS kg_nodes_aliases_trgm
    ON kg_nodes USING GIN (aliases_flat gin_trgm_ops);

-- Replace rag_resolve_entity_anchors to also match aliases + emit matched_via.
-- Tier ordering: name → alias → tag → trgm (strict tiers take priority).
-- The trgm tier uses the % operator (pg_trgm similarity ≥ pg_trgm.similarity_threshold
-- default 0.3) against aliases_flat for fuzzy/typo recall.
-- Return signature adds matched_via text while keeping node_id first column so
-- existing callers that only read node_id are unaffected.
CREATE OR REPLACE FUNCTION rag_resolve_entity_anchors(p_sandbox_id uuid, p_entities text[])
RETURNS TABLE (node_id text, matched_via text)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (n.id)
        n.id AS node_id,
        CASE
            WHEN EXISTS (
                SELECT 1 FROM unnest(p_entities) e
                WHERE n.name ILIKE '%' || e || '%'
            ) THEN 'name'
            WHEN EXISTS (
                SELECT 1 FROM unnest(p_entities) e
                         , unnest(n.aliases) a
                WHERE a ILIKE '%' || e || '%'
            ) THEN 'alias'
            WHEN EXISTS (
                SELECT 1 FROM unnest(p_entities) e
                WHERE e = ANY(n.tags)
            ) THEN 'tag'
            WHEN EXISTS (
                SELECT 1 FROM unnest(p_entities) e
                WHERE n.aliases_flat % e
            ) THEN 'trgm'
            ELSE 'name'
        END AS matched_via
    FROM rag_sandbox_members m
    JOIN kg_nodes n
      ON n.id = m.node_id
     AND n.user_id = m.user_id
    WHERE m.sandbox_id = p_sandbox_id
      AND (
        EXISTS (
            SELECT 1 FROM unnest(p_entities) e
            WHERE n.name ILIKE '%' || e || '%'
        )
        OR EXISTS (
            SELECT 1 FROM unnest(p_entities) e
                     , unnest(n.aliases) a
            WHERE a ILIKE '%' || e || '%'
        )
        OR EXISTS (
            SELECT 1 FROM unnest(p_entities) e
            WHERE e = ANY(n.tags)
        )
        OR EXISTS (
            SELECT 1 FROM unnest(p_entities) e
            WHERE n.aliases_flat % e
        )
      )
$$;
