-- DB v2 KG schema: graph nodes, edges, mentions, and subgraph expansion.

CREATE SCHEMA IF NOT EXISTS kg;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'kg_edge_relation' AND typnamespace = 'kg'::regnamespace) THEN
        CREATE TYPE kg.kg_edge_relation AS ENUM (
            'shared_tag', 'cites', 'mentions', 'co_occurs', 'authored_by', 'published_in'
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS kg.kg_nodes (
    id              bigserial PRIMARY KEY,
    workspace_id    uuid REFERENCES core.workspaces(id) ON DELETE CASCADE,
    workspace_key   text GENERATED ALWAYS AS (COALESCE(workspace_id::text, '__global__')) STORED,
    type            text NOT NULL,
    canonical_name  text NOT NULL,
    slug            text NOT NULL,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_key, slug)
);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_canonical_name
    ON kg.kg_nodes(canonical_name);

CREATE TABLE IF NOT EXISTS kg.kg_edges (
    id                           bigserial PRIMARY KEY,
    workspace_id                 uuid REFERENCES core.workspaces(id) ON DELETE CASCADE,
    workspace_key                text GENERATED ALWAYS AS (COALESCE(workspace_id::text, '__global__')) STORED,
    src_node_id                  bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
    dst_node_id                  bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
    relation_type                kg.kg_edge_relation NOT NULL,
    shared_tag_label             text,
    weight                       numeric,
    evidence_canonical_zettel_id uuid REFERENCES content.canonical_zettels(id) ON DELETE SET NULL,
    metadata                     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at                   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_workspace_src ON kg.kg_edges (workspace_key, src_node_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_workspace_dst ON kg.kg_edges (workspace_key, dst_node_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON kg.kg_edges (relation_type);

CREATE TABLE IF NOT EXISTS kg.chunk_node_mentions (
    canonical_chunk_id uuid NOT NULL REFERENCES content.canonical_chunks(id) ON DELETE CASCADE,
    kg_node_id         bigint NOT NULL REFERENCES kg.kg_nodes(id) ON DELETE CASCADE,
    mention_type       text NOT NULL CHECK (mention_type IN ('extracted', 'tagged', 'derived', 'authored')),
    score              numeric,
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (canonical_chunk_id, kg_node_id, mention_type)
);

CREATE INDEX IF NOT EXISTS idx_chunk_node_mentions_node
    ON kg.chunk_node_mentions(kg_node_id);

CREATE OR REPLACE FUNCTION kg.expand_subgraph(
    p_workspace_id uuid,
    p_node_ids bigint[],
    p_depth int DEFAULT 1
) RETURNS TABLE(id bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
        WITH RECURSIVE walk AS (
            SELECT unnest(p_node_ids) AS id, 0 AS d
            UNION ALL
            SELECT CASE WHEN e.src_node_id = w.id THEN e.dst_node_id ELSE e.src_node_id END AS id,
                   w.d + 1 AS d
              FROM kg.kg_edges e
              JOIN walk w ON e.src_node_id = w.id OR e.dst_node_id = w.id
             WHERE e.workspace_id = p_workspace_id
               AND w.d < p_depth
        )
        SELECT DISTINCT walk.id FROM walk;
END
$$;

GRANT EXECUTE ON FUNCTION kg.expand_subgraph(uuid, bigint[], int) TO authenticated;

