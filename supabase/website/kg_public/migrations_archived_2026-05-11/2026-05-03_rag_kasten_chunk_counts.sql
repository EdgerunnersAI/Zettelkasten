-- iter-08 Phase 4: per-Kasten chunk count for chunk-share anti-magnet.
-- Replaces dead kasten_freq prior (RES-2: floor=50 never crossed). Used by
-- ChunkShareStore in website/features/rag_pipeline/retrieval/chunk_share.py
-- to damp rrf_score by 1/sqrt(chunk_count_per_node) post-fusion.
create or replace function rag_kasten_chunk_counts(p_sandbox_id uuid)
returns table (node_id text, chunk_count int)
language sql stable as $$
    select m.node_id, count(c.id)::int as chunk_count
    from rag_sandbox_members m
    left join kg_node_chunks c
      on c.node_id = m.node_id
     and c.user_id = m.user_id
    where m.sandbox_id = p_sandbox_id
    group by m.node_id
$$;
