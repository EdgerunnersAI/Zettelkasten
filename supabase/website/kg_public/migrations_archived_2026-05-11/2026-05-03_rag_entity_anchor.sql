-- iter-08 Phase 6: entity-name → anchor-node resolver. Fuzzy match via
-- title ILIKE %entity% (cheap; pg_trgm index on kg_nodes.name helps).
-- Uses kg_nodes.name (canonical title column per schema.sql line 39).
-- Composite PK (user_id, id) requires joining on both columns.
create or replace function rag_resolve_entity_anchors(p_sandbox_id uuid, p_entities text[])
returns table (node_id text)
language sql stable as $$
    select distinct n.id as node_id
    from rag_sandbox_members m
    join kg_nodes n
      on n.id = m.node_id
     and n.user_id = m.user_id
    where m.sandbox_id = p_sandbox_id
      and exists (
        select 1 from unnest(p_entities) e
        where n.name ILIKE '%' || e || '%' or e = ANY(n.tags)
      )
$$;

-- 1-hop neighbours via kg_links (plural). Edges are directed-stored but treated
-- as undirected for neighbour expansion. Returns node_ids on either end of any
-- edge touching an anchor, scoped to Kasten members.
create or replace function rag_one_hop_neighbours(p_sandbox_id uuid, p_anchor_nodes text[])
returns table (node_id text)
language sql stable as $$
    select distinct nbr as node_id
    from (
        select case when l.source_node_id = any(p_anchor_nodes)
                    then l.target_node_id
                    else l.source_node_id end as nbr
        from kg_links l
        join rag_sandbox_members m_anchor
          on (m_anchor.node_id = l.source_node_id and m_anchor.user_id = l.user_id)
          or (m_anchor.node_id = l.target_node_id and m_anchor.user_id = l.user_id)
        where m_anchor.sandbox_id = p_sandbox_id
          and (l.source_node_id = any(p_anchor_nodes) or l.target_node_id = any(p_anchor_nodes))
    ) edges
    join rag_sandbox_members m_nbr
      on m_nbr.node_id = edges.nbr
     and m_nbr.sandbox_id = p_sandbox_id
$$;
