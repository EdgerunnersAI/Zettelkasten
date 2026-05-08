-- ============================================================================
-- 2026-04-26_fix_rag_bulk_add_to_sandbox.sql
--
-- Fixes iter-06 README line 127: "rag_bulk_add_to_sandbox RPC returns
-- added_count=0 even with valid (user_id, sandbox_id, node_ids); direct
-- rag_sandbox_members.insert works."
--
-- Root-cause hypothesis (NOT live-verified — see DEPLOY NOTE below):
--   Two confirmed contributors observed in iter-06:
--   (a) iter-06 README line 31: "Constraint required added_via='manual'
--       (not 'rag_eval_iter06')." The CHECK constraint on rag_sandbox_members
--       (003_sandboxes.sql lines 34-35) restricts added_via to
--       ('manual','bulk_tag','bulk_source','graph_pick','migration'). Any
--       caller passing a value outside this set causes the INSERT…SELECT to
--       fail mid-statement, but because the failure happens inside a CTE
--       wrapped by `ON CONFLICT (sandbox_id, node_id) DO NOTHING`, supabase-py
--       can surface this as `data=0` rather than raising. The original RPC
--       defaulted p_added_via='bulk_tag' which is valid; however any caller
--       overriding it (e.g. iter-06 harness) silently drops every row.
--   (b) Defensive: `p_node_ids text[]` works for kg_nodes.id (text PK), but
--       the RLS context inside `SECURITY DEFINER … SET search_path=''` may
--       not see the request.jwt.claims required by the rag_sandbox_members
--       INSERT policy. The original is already `SECURITY DEFINER`, so the
--       INSERT should bypass RLS — but if any policy was added later
--       requiring `auth.uid()`, SECURITY DEFINER alone is insufficient
--       because `auth.uid()` returns NULL when the function runs as the
--       definer's role. We force-disable per-row RLS evaluation inside the
--       function via the existing SECURITY DEFINER + explicit search_path.
--
-- Fix:
--   1. Validate p_added_via against the known set; raise a clear EXCEPTION
--      instead of silently dropping rows.
--   2. Capture the candidate set explicitly, then compute dropped_node_ids
--      = candidates - inserted - already-members so callers can detect
--      partial drops.
--   3. Return a jsonb object {added_count, dropped_node_ids, candidate_count}
--      so the supabase-py client can assert on a structured payload (the
--      previous int return masked failure modes).
--   4. Re-affirm SECURITY DEFINER + explicit search_path; cast p_node_ids
--      defensively; keep ON CONFLICT (sandbox_id, node_id) DO NOTHING which
--      is the legitimate idempotency key per rag_sandbox_members PK.
--
-- DEPLOY NOTE:
--   This migration was authored without live staging credentials in the
--   sandbox where it was written. The deploy-time runner MUST:
--     1. Apply via `psql $SUPABASE_DB_URL -f
--        supabase/website/kg_public/migrations/2026-04-26_fix_rag_bulk_add_to_sandbox.sql`
--     2. Run `pytest tests/integration_tests/test_rag_sandbox_rpc.py --live -v`
--        with NARUTO_USER_ID and TEST_SANDBOX_ID set.
--     3. Confirm the test passes (added_count == 2) before promoting to prod.
--
-- Backward compatibility:
--   RETURN TYPE CHANGES from `int` to `jsonb`. The Python caller in
--   `website/features/rag_pipeline/memory/sandbox_store.py::SandboxStore.add_members`
--   is updated in the same change to read `data["added_count"]`.
-- ============================================================================

DROP FUNCTION IF EXISTS public.rag_bulk_add_to_sandbox(uuid, uuid, text[], text, text[], text[], text);

CREATE OR REPLACE FUNCTION public.rag_bulk_add_to_sandbox(
    p_user_id      uuid,
    p_sandbox_id   uuid,
    p_tags         text[] DEFAULT NULL,
    p_tag_mode     text   DEFAULT 'all',
    p_source_types text[] DEFAULT NULL,
    p_node_ids     text[] DEFAULT NULL,
    p_added_via    text   DEFAULT 'manual'
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = ''
SET statement_timeout = '10s'
AS $$
DECLARE
    n_added           int;
    n_candidates      int;
    v_dropped         text[];
    v_allowed_via     text[] := ARRAY['manual','bulk_tag','bulk_source','graph_pick','migration'];
BEGIN
    -- Guard: caller must pass an added_via value that satisfies the
    -- rag_sandbox_members CHECK constraint. Previously, an out-of-set value
    -- caused the INSERT to error inside the CTE and the wrapper returned 0
    -- with no signal — see iter-06 README line 31.
    IF NOT (p_added_via = ANY(v_allowed_via)) THEN
        RAISE EXCEPTION
            'rag_bulk_add_to_sandbox: invalid p_added_via=%; must be one of %',
            p_added_via, v_allowed_via
            USING ERRCODE = 'check_violation';
    END IF;

    -- Verify the sandbox belongs to the requesting user before any insert.
    IF NOT EXISTS (
        SELECT 1 FROM public.rag_sandboxes
         WHERE id = p_sandbox_id AND user_id = p_user_id
    ) THEN
        RAISE EXCEPTION
            'rag_bulk_add_to_sandbox: sandbox % not found for user %',
            p_sandbox_id, p_user_id
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Resolve candidate node ids once, materialize so we can diff against
    -- the inserted rows for dropped-row observability.
    CREATE TEMP TABLE _rag_bulk_add_candidates ON COMMIT DROP AS
    SELECT id
      FROM public.kg_nodes
     WHERE user_id = p_user_id
       AND (p_node_ids     IS NULL OR id          = ANY(p_node_ids::text[]))
       AND (p_tags         IS NULL OR (
            (p_tag_mode = 'all' AND tags @> p_tags) OR
            (p_tag_mode = 'any' AND tags && p_tags)
            ))
       AND (p_source_types IS NULL OR source_type = ANY(p_source_types));

    SELECT COUNT(*) INTO n_candidates FROM _rag_bulk_add_candidates;

    WITH inserted AS (
        INSERT INTO public.rag_sandbox_members
            (sandbox_id, user_id, node_id, added_via, added_filter)
        SELECT
            p_sandbox_id, p_user_id, c.id, p_added_via,
            jsonb_build_object(
                'tags',         p_tags,
                'tag_mode',     p_tag_mode,
                'source_types', p_source_types
            )
        FROM _rag_bulk_add_candidates c
        ON CONFLICT (sandbox_id, node_id) DO NOTHING
        RETURNING node_id
    )
    SELECT COUNT(*) INTO n_added FROM inserted;

    -- Compute dropped: candidate rows that ended up in neither the new
    -- insert nor the pre-existing membership set. Should be empty under
    -- normal operation; non-empty signals an unhandled INSERT failure.
    SELECT COALESCE(array_agg(c.id), ARRAY[]::text[])
      INTO v_dropped
      FROM _rag_bulk_add_candidates c
     WHERE NOT EXISTS (
            SELECT 1 FROM public.rag_sandbox_members m
             WHERE m.sandbox_id = p_sandbox_id
               AND m.node_id    = c.id
       );

    -- Touch last_used_at on the sandbox.
    UPDATE public.rag_sandboxes
       SET last_used_at = now(), updated_at = now()
     WHERE id = p_sandbox_id AND user_id = p_user_id;

    RETURN jsonb_build_object(
        'added_count',      n_added,
        'candidate_count',  n_candidates,
        'dropped_node_ids', to_jsonb(v_dropped)
    );
END;
$$;

COMMENT ON FUNCTION public.rag_bulk_add_to_sandbox IS
    'Bulk add Zettels to a sandbox. Returns jsonb {added_count, candidate_count, dropped_node_ids}. Validates added_via against rag_sandbox_members CHECK constraint and raises on invalid input rather than silently dropping rows (iter-06 fix).';

-- Permissions (re-grant since DROP FUNCTION above removed them).
REVOKE ALL ON FUNCTION public.rag_bulk_add_to_sandbox(uuid, uuid, text[], text, text[], text[], text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.rag_bulk_add_to_sandbox(uuid, uuid, text[], text, text[], text[], text)
    TO authenticated, service_role;
