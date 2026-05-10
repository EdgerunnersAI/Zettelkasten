-- Phase 7.2-deferred: kasten member-sharing — RLS + auto-owner-row.
-- Originally deferred from Phase 4.4; closing the gap so kasten sharing works
-- end-to-end:
--   1. Owner workspace creates a kasten in rag.kastens.
--   2. trg_auto_kasten_owner_member auto-inserts a rag.kasten_members row
--      with role='owner' for the owning workspace.
--   3. The pre-existing rag.assert_kasten_owner_can_grant trigger then accepts
--      additional non-owner member rows when the granter's JWT carries
--      app_metadata role='owner' on the workspace whose kasten_members row
--      is the owner row.
--   4. The extended SELECT policies below let recipient workspaces read
--      rag.kastens + rag.kasten_zettels via the kasten_members join.
--
-- Idempotent: every CREATE FUNCTION uses OR REPLACE; every trigger is
-- DROP-IF-EXISTS-then-CREATE; every policy is DROP-IF-EXISTS-then-CREATE.
-- Safe to re-apply.

-- ─────────────────────────────────────────────────────────────────────
-- 1. Auto-owner-row trigger
-- ─────────────────────────────────────────────────────────────────────
-- On INSERT into rag.kastens, auto-insert a rag.kasten_members row for
-- the owning workspace with role='owner'. ON CONFLICT DO NOTHING keeps
-- the trigger idempotent under retries / explicit pre-seeded owner rows.
--
-- Interaction with rag.assert_kasten_owner_can_grant: that trigger fires
-- on rag.kasten_members INSERT. When NEW.role='owner' the assertion is
-- short-circuited (NEW.role <> 'owner' is false), so the bootstrap insert
-- here cannot deadlock on its own grant check.

CREATE OR REPLACE FUNCTION rag.fn_auto_kasten_owner_member()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO rag.kasten_members (kasten_id, workspace_id, role)
    VALUES (NEW.id, NEW.workspace_id, 'owner')
    ON CONFLICT (kasten_id, workspace_id) DO NOTHING;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_auto_kasten_owner_member ON rag.kastens;
CREATE TRIGGER trg_auto_kasten_owner_member
    AFTER INSERT ON rag.kastens
    FOR EACH ROW
    EXECUTE FUNCTION rag.fn_auto_kasten_owner_member();

-- Backfill: ensure every existing kasten has its owning-workspace owner row.
INSERT INTO rag.kasten_members (kasten_id, workspace_id, role)
SELECT k.id, k.workspace_id, 'owner'
  FROM rag.kastens k
  LEFT JOIN rag.kasten_members km
    ON km.kasten_id = k.id AND km.workspace_id = k.workspace_id
 WHERE km.kasten_id IS NULL
ON CONFLICT (kasten_id, workspace_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 2. Extend rag.kastens SELECT to include the kasten_members join
-- ─────────────────────────────────────────────────────────────────────
-- Replaces the workspace-only kastens_workspace_select policy from
-- _v2/08_rls_policies.sql line 195. Members of any role on a kasten can
-- now read the kasten row — required for shared kastens to surface in the
-- recipient's UI. service_role bypass is preserved.

DROP POLICY IF EXISTS kastens_workspace_select ON rag.kastens;
DROP POLICY IF EXISTS kastens_member_or_owner_select ON rag.kastens;
CREATE POLICY kastens_member_or_owner_select ON rag.kastens
    FOR SELECT TO authenticated USING (
        workspace_id = ANY (core.jwt_workspace_ids())
        OR EXISTS (
            SELECT 1 FROM rag.kasten_members km
             WHERE km.kasten_id = rag.kastens.id
               AND km.workspace_id = ANY (core.jwt_workspace_ids())
        )
    );

-- ─────────────────────────────────────────────────────────────────────
-- 3. Extend rag.kasten_zettels SELECT through the kasten_members join
-- ─────────────────────────────────────────────────────────────────────
-- Replaces kasten_zettels_workspace_select from _v2/08_rls_policies.sql
-- line 217. Recipients of a shared kasten can read its zettel rows.
-- INSERT/DELETE policies on kasten_zettels are unchanged — only the
-- owning workspace can mutate kasten_zettels (owner/editor role required).

DROP POLICY IF EXISTS kasten_zettels_workspace_select ON rag.kasten_zettels;
DROP POLICY IF EXISTS kasten_zettels_member_or_owner_select ON rag.kasten_zettels;
CREATE POLICY kasten_zettels_member_or_owner_select ON rag.kasten_zettels
    FOR SELECT TO authenticated USING (
        EXISTS (
            SELECT 1 FROM rag.kastens k
             WHERE k.id = rag.kasten_zettels.kasten_id
               AND (
                   k.workspace_id = ANY (core.jwt_workspace_ids())
                   OR EXISTS (
                       SELECT 1 FROM rag.kasten_members km
                        WHERE km.kasten_id = k.id
                          AND km.workspace_id = ANY (core.jwt_workspace_ids())
                   )
               )
        )
    );

-- The pre-existing rag.assert_kasten_owner_can_grant trigger
-- (_v2/04_rag_schema.sql line 157) requires no change: with the
-- auto-owner-row trigger above, every kasten now has the granter-side
-- owner row at grant time, so the trigger's EXISTS check passes for the
-- creator-workspace JWT (which holds app_metadata.role='owner' on its
-- own personal/owned workspace).

-- ─────────────────────────────────────────────────────────────────────
-- 4. Extend rag.list_kasten_zettels to honour kasten_members
-- ─────────────────────────────────────────────────────────────────────
-- The original RPC (_v2/13_v2_kasten_rpcs.sql line 171) checks only
-- workspace_id = ANY(jwt_workspace_ids()) before returning rows. With
-- member-sharing, recipient workspaces must be able to call this RPC and
-- see the joined zettel list. service_role bypass is preserved.
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
          AND (
              k.workspace_id = ANY (core.jwt_workspace_ids())
              OR EXISTS (
                  SELECT 1 FROM rag.kasten_members km
                  WHERE km.kasten_id = k.id
                    AND km.workspace_id = ANY (core.jwt_workspace_ids())
              )
              OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
          )
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

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
