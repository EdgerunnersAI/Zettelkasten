-- Phase 1.D.6: entities -> anchor chunks bridge RPC.
-- Given a set of kg_node ids (typically resolved by resolve_entity_anchors_v2),
-- return the canonical_chunk_ids that mention them, with mention counts so
-- callers can rank "anchor chunks" for downstream RAG fanout.
--
-- mention_count is count(*) across mention_type rows for a given
-- (canonical_chunk_id, kg_node_id) pair — kg.chunk_node_mentions is keyed
-- by (canonical_chunk_id, kg_node_id, mention_type) so multiple types per
-- pair are possible (extracted/tagged/derived/authored).

CREATE INDEX IF NOT EXISTS idx_chunk_node_mentions_node_chunk
    ON kg.chunk_node_mentions (kg_node_id, canonical_chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_node_mentions_chunk_node
    ON kg.chunk_node_mentions (canonical_chunk_id, kg_node_id);

CREATE OR REPLACE FUNCTION kg.entities_to_anchor_chunks(
    p_workspace_id  uuid,
    p_kg_node_ids   bigint[]
) RETURNS TABLE (
    canonical_chunk_id uuid,
    kg_node_id         bigint,
    mention_count      int
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())
            OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role') THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    IF p_kg_node_ids IS NULL OR cardinality(p_kg_node_ids) = 0 THEN
        RETURN;
    END IF;
    RETURN QUERY
        SELECT cnm.canonical_chunk_id,
               cnm.kg_node_id,
               count(*)::int AS mention_count
          FROM kg.chunk_node_mentions cnm
          JOIN kg.kg_nodes n ON n.id = cnm.kg_node_id
         WHERE n.workspace_id = p_workspace_id
           AND cnm.kg_node_id = ANY (p_kg_node_ids)
         GROUP BY cnm.canonical_chunk_id, cnm.kg_node_id;
END $$;
GRANT EXECUTE ON FUNCTION kg.entities_to_anchor_chunks(uuid, bigint[])
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
