-- _v2/13_v2_kasten_rpcs.sql — kasten retrieval RPCs + supporting indexes.
--
-- Phase 1.A of the v2 purge: ship 5 SECURITY DEFINER RPCs that mediate
-- workspace-scoped reads against rag.kastens / rag.kasten_zettels and the
-- canonical chunk graph. Authorisation goes through core.jwt_workspace_ids()
-- (workspace membership baked into the JWT app_metadata) OR an explicit
-- service-role check via the request JWT claims.
--
-- All RPCs:
--   * SECURITY DEFINER + SET search_path = public (search-path trojan guard).
--   * Raise SQLSTATE 42501 ('unauthorized') for any non-service-role caller
--     that does not own the kasten / is not a member of the workspace.
--   * Granted to authenticated and service_role only.
--
-- The two new indexes (R2.3, R2.8) are workload-driven: the composite INCLUDE
-- index on retrieval_signal_weights backs search_signal_weights' ANY-array
-- lookup, and the FK-direction index on kasten_zettels (workspace_zettel_id)
-- prevents the slow seq-scan when content.workspace_zettels rows are deleted.

-- ── 0. Extend rag.kasten_zettels.added_via to allow 'bulk_rpc' ──────────────
-- The bulk_add_to_kasten RPC tags inserts with added_via='bulk_rpc' so the
-- audit trail in kasten_zettels can distinguish RPC-driven bulk adds from the
-- pre-existing manual / bulk_tag / bulk_source / graph_pick / migration paths.
-- Done inline (not in 04_rag_schema.sql) so the canonical schema file stays a
-- pristine first-install snapshot; the ALTER lives with the consumer RPC.
ALTER TABLE rag.kasten_zettels
    DROP CONSTRAINT IF EXISTS kasten_zettels_added_via_check;
ALTER TABLE rag.kasten_zettels
    ADD CONSTRAINT kasten_zettels_added_via_check
    CHECK (added_via IN ('manual', 'bulk_tag', 'bulk_source', 'graph_pick', 'migration', 'bulk_rpc'));

-- ── 1. rag.search_signal_weights ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rag.search_signal_weights(
    p_workspace_id      uuid,
    p_target_chunk_ids  uuid[],
    p_query_class       text
) RETURNS TABLE (
    source_canonical_chunk_id uuid,
    target_canonical_chunk_id uuid,
    weight                    double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())
            OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role') THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT rsw.source_canonical_chunk_id,
               rsw.target_canonical_chunk_id,
               rsw.weight
          FROM rag.retrieval_signal_weights rsw
         WHERE rsw.workspace_id = p_workspace_id
           AND rsw.query_class  = p_query_class
           AND rsw.target_canonical_chunk_id = ANY (p_target_chunk_ids);
END $$;
GRANT EXECUTE ON FUNCTION rag.search_signal_weights(uuid, uuid[], text)
    TO authenticated, service_role;

-- ── 2. rag.chunk_share_for_kasten ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION rag.chunk_share_for_kasten(p_kasten_id uuid)
RETURNS TABLE (canonical_chunk_id uuid, chunk_count int)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT wcm.canonical_chunk_id, count(*)::int AS chunk_count
          FROM rag.kasten_zettels kz
          JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
          JOIN content.workspace_chunk_membership wcm
            ON wcm.workspace_zettel_id = wz.id
         WHERE kz.kasten_id = p_kasten_id
         GROUP BY wcm.canonical_chunk_id;
END $$;
GRANT EXECUTE ON FUNCTION rag.chunk_share_for_kasten(uuid) TO authenticated, service_role;

-- ── 3. rag.bulk_add_to_kasten ───────────────────────────────────────────────
-- Authorise via kasten ownership (NOT a passed-in workspace_id, audit D.3).
CREATE OR REPLACE FUNCTION rag.bulk_add_to_kasten(
    p_kasten_id            uuid,
    p_workspace_zettel_ids uuid[]
) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    inserted_count int := 0;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
    SELECT p_kasten_id, wz_id, 'bulk_rpc'
      FROM unnest(p_workspace_zettel_ids) wz_id
    ON CONFLICT (kasten_id, workspace_zettel_id) DO NOTHING;
    GET DIAGNOSTICS inserted_count = ROW_COUNT;
    RETURN inserted_count;
END $$;
GRANT EXECUTE ON FUNCTION rag.bulk_add_to_kasten(uuid, uuid[]) TO authenticated, service_role;

-- ── 4. rag.fetch_anchor_seeds_v2 ────────────────────────────────────────────
-- Round-2 R2.4: NO set_config('hnsw.iterative_scan',…) — dead code given the
-- explicit ID filter (cc.id = ANY (p_anchor_canonical_chunk_ids)) which makes
-- the planner pick the PK index, not the HNSW index, regardless of pgvector
-- iterative-scan mode.
CREATE OR REPLACE FUNCTION rag.fetch_anchor_seeds_v2(
    p_kasten_id        uuid,
    p_anchor_canonical_chunk_ids uuid[],
    p_query_embedding  halfvec(768)
) RETURNS TABLE (
    canonical_chunk_id  uuid,
    canonical_zettel_id uuid,
    chunk_idx           int,
    content             text,
    score               double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        WITH ranked AS (
            SELECT
                cc.id AS canonical_chunk_id,
                cc.canonical_zettel_id,
                cc.chunk_idx,
                cc.content,
                (1 - (cc.embedding <=> p_query_embedding))::double precision AS score,
                ROW_NUMBER() OVER (
                    PARTITION BY cc.canonical_zettel_id
                    ORDER BY cc.embedding <=> p_query_embedding ASC
                ) AS rn
              FROM rag.kasten_zettels kz
              JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
              JOIN content.workspace_chunk_membership wcm
                ON wcm.workspace_zettel_id = wz.id
              JOIN content.canonical_chunks cc
                ON cc.id = wcm.canonical_chunk_id
             WHERE kz.kasten_id = p_kasten_id
               AND cc.id = ANY (p_anchor_canonical_chunk_ids)
        )
        SELECT ranked.canonical_chunk_id,
               ranked.canonical_zettel_id,
               ranked.chunk_idx,
               ranked.content,
               ranked.score
          FROM ranked
         WHERE ranked.rn = 1
         ORDER BY ranked.score DESC
         LIMIT 8;
END $$;
GRANT EXECUTE ON FUNCTION rag.fetch_anchor_seeds_v2(uuid, uuid[], halfvec) TO authenticated, service_role;

-- ── 5. rag.list_kasten_zettels ──────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rag.list_kasten_zettels(p_kasten_id uuid)
RETURNS TABLE (
    workspace_zettel_id   uuid,
    canonical_zettel_id   uuid,
    title                 text,
    source_type           text,
    user_tags             text[],
    ai_summary            text,
    added_at              timestamptz
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT wz.id, cz.id AS canonical_zettel_id, cz.title, cz.source_type,
               wz.user_tags, wz.ai_summary, kz.added_at
          FROM rag.kasten_zettels kz
          JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
          JOIN content.canonical_zettels cz ON cz.id = wz.canonical_zettel_id
         WHERE kz.kasten_id = p_kasten_id
           AND wz.deleted_at IS NULL;
END $$;
GRANT EXECUTE ON FUNCTION rag.list_kasten_zettels(uuid) TO authenticated, service_role;

-- ── 6. R2.3 — composite INCLUDE index on rag.retrieval_signal_weights ───────
CREATE INDEX IF NOT EXISTS idx_retrieval_signal_workspace_class_target
    ON rag.retrieval_signal_weights (workspace_id, query_class, target_canonical_chunk_id)
    INCLUDE (source_canonical_chunk_id, weight);

-- ── 7. R2.8 — reverse-direction FK index on rag.kasten_zettels ──────────────
CREATE INDEX IF NOT EXISTS idx_kasten_zettels_workspace_zettel
    ON rag.kasten_zettels (workspace_zettel_id);

-- ── 8. PostgREST schema-cache reload ────────────────────────────────────────
-- Round-2 R2.10: 2-second post-migration sleep is documented in the plan;
-- not modelled in SQL.
NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
