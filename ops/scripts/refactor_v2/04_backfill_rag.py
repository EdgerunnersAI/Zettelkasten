"""Backfill rag.kastens, kasten memberships, sessions, and messages."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import run_statements, load_config, parse_args, require_continue, run_async


SQL = """
DO $$
BEGIN
    IF to_regclass('public.rag_sandboxes') IS NULL THEN
        RAISE NOTICE 'legacy RAG tables are absent; skipping RAG backfill';
        RETURN;
    END IF;
END $$;

INSERT INTO rag.kastens (
    id, workspace_id, name, description, icon, color, default_quality,
    last_used_at, created_at, updated_at
)
SELECT s.id,
       w.id,
       s.name,
       s.description,
       s.icon,
       s.color,
       s.default_quality,
       s.last_used_at,
       s.created_at,
       s.updated_at
  FROM public.rag_sandboxes s
  JOIN public.kg_users ku ON ku.id = s.user_id
  JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    default_quality = EXCLUDED.default_quality,
    updated_at = now();

INSERT INTO rag.kasten_members (kasten_id, workspace_id, role, added_at)
SELECT k.id, k.workspace_id, 'owner', k.created_at
  FROM rag.kastens k
ON CONFLICT DO NOTHING;

WITH mapped AS (
    SELECT s.id AS kasten_id,
           wz.id AS workspace_zettel_id,
           m.added_via,
           m.added_filter,
           m.added_at
      FROM public.rag_sandbox_members m
      JOIN public.rag_sandboxes s ON s.id = m.sandbox_id
      JOIN public.kg_users ku ON ku.id = m.user_id
      JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
      JOIN public.kg_nodes n ON n.user_id = m.user_id AND n.id = m.node_id
      JOIN content.canonical_zettels cz
        ON cz.normalized_url = n.url
       AND cz.content_hash = digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256')
      JOIN content.workspace_zettels wz ON wz.workspace_id = w.id AND wz.canonical_zettel_id = cz.id
)
INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via, added_filter, added_at)
SELECT kasten_id, workspace_zettel_id, added_via, added_filter, added_at
  FROM mapped
ON CONFLICT DO NOTHING;

INSERT INTO rag.chat_sessions (id, workspace_id, profile_id, kasten_id, title, created_at, updated_at)
SELECT cs.id,
       w.id,
       ku.render_user_id::uuid,
       cs.sandbox_id,
       cs.title,
       cs.created_at,
       cs.updated_at
  FROM public.chat_sessions cs
  JOIN public.kg_users ku ON ku.id = cs.user_id
  JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
ON CONFLICT (id) DO UPDATE
SET title = EXCLUDED.title,
    updated_at = now();

WITH message_rows AS (
    SELECT cm.id,
           cm.session_id,
           rs.workspace_id,
           cm.role,
           cm.content,
           (
             coalesce(cm.citations, '[]'::jsonb) ||
             coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'canonical_chunk_id', cc.id::text,
                    'legacy_chunk_id', legacy_chunk_id::text
                ))
                  FROM unnest(cm.retrieved_chunk_ids) AS legacy_chunk_id
                  JOIN public.kg_node_chunks oldc ON oldc.id = legacy_chunk_id
                  JOIN public.kg_nodes oldn ON oldn.user_id = oldc.user_id AND oldn.id = oldc.node_id
                  JOIN content.canonical_zettels cz
                    ON cz.normalized_url = oldn.url
                   AND cz.content_hash = digest(coalesce(oldn.url, '') || E'\n' || coalesce(oldn.summary, ''), 'sha256')
                  JOIN content.canonical_chunks cc
                    ON cc.canonical_zettel_id = cz.id
                   AND cc.chunk_idx = oldc.chunk_idx
             ), '[]'::jsonb)
           ) AS citations,
           CASE
             WHEN cm.critic_verdict IN ('supported', 'retried_supported', 'partial') THEN cm.critic_verdict
             WHEN cm.critic_verdict IN ('unsupported', 'retried_still_bad', 'retried_low_confidence') THEN 'unsupported'
             ELSE NULL
           END AS verdict,
           cm.token_counts,
           cm.latency_ms,
           cm.created_at
      FROM public.chat_messages cm
      JOIN rag.chat_sessions rs ON rs.id = cm.session_id
)
INSERT INTO rag.chat_messages (
    id, session_id, workspace_id, role, content, citations, verdict,
    token_counts, latency_ms, created_at
)
SELECT id, session_id, workspace_id, role, content, citations, verdict,
       token_counts, latency_ms, created_at
  FROM message_rows
ON CONFLICT (id) DO NOTHING;
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "RAG backfill")
    if args.dry_run:
        print("rag backfill ready")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
