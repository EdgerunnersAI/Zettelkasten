-- Phase 7.2: drop legacy v1 RPC zombies that reference dropped tables.
-- Phase 6 dropped the underlying public.* tables (kg_users, kg_nodes, kg_links,
-- rag_sandboxes, rag_sandbox_members, kg_node_chunks, etc.); these RPCs would
-- fail at runtime. Signatures captured from pg_proc on 2026-05-10.
DROP FUNCTION IF EXISTS public.rag_resolve_entity_anchors(uuid, text[]) CASCADE;
DROP FUNCTION IF EXISTS public.rag_one_hop_neighbours(uuid, text[]) CASCADE;
DROP FUNCTION IF EXISTS public.rag_fetch_anchor_seeds(uuid, text[], vector) CASCADE;
DROP FUNCTION IF EXISTS public.rag_dense_recall(uuid, text[], vector, integer) CASCADE;
DROP FUNCTION IF EXISTS public.rag_hybrid_search(
    uuid, text, vector, text[], integer,
    double precision, double precision, double precision,
    integer, integer, double precision
) CASCADE;
DROP FUNCTION IF EXISTS public.rag_kasten_chunk_counts(uuid) CASCADE;
DROP FUNCTION IF EXISTS public.rag_resolve_effective_nodes(
    uuid, uuid, text[], text[], text, text[]
) CASCADE;
NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
