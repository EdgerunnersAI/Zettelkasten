"""Backfill kg.kg_nodes, kg.kg_edges, and kg.chunk_node_mentions."""

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
    IF to_regclass('public.kg_nodes') IS NULL THEN
        RAISE NOTICE 'legacy KG tables are absent; skipping KG backfill';
        RETURN;
    END IF;
END $$;

INSERT INTO kg.kg_nodes (workspace_id, type, canonical_name, slug, metadata, created_at)
SELECT w.id,
       'zettel',
       n.name,
       n.id,
       jsonb_build_object(
           'legacy_user_id', n.user_id,
           'source_type', n.source_type,
           'url', n.url,
           'tags', n.tags,
           'aliases', n.aliases
       ) || coalesce(n.metadata, '{}'::jsonb),
       n.created_at
  FROM public.kg_nodes n
  JOIN public.kg_users ku ON ku.id = n.user_id
  JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
ON CONFLICT (workspace_key, slug) DO UPDATE
SET canonical_name = EXCLUDED.canonical_name,
    metadata = EXCLUDED.metadata;

INSERT INTO kg.kg_edges (
    workspace_id, src_node_id, dst_node_id, relation_type, shared_tag_label,
    evidence_canonical_zettel_id, metadata, created_at
)
SELECT w.id,
       src.id,
       dst.id,
       COALESCE(l.relation_type::text, 'shared_tag')::kg.kg_edge_relation,
       l.relation,
       cz.id,
       jsonb_build_object('legacy_edge_id', l.id, 'legacy_user_id', l.user_id),
       l.created_at
  FROM public.kg_links l
  JOIN public.kg_users ku ON ku.id = l.user_id
  JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
  JOIN kg.kg_nodes src ON src.workspace_id = w.id AND src.slug = l.source_node_id
  JOIN kg.kg_nodes dst ON dst.workspace_id = w.id AND dst.slug = l.target_node_id
  LEFT JOIN public.kg_nodes n ON n.user_id = l.user_id AND n.id = l.source_node_id
  LEFT JOIN content.canonical_zettels cz
    ON cz.normalized_url = n.url
   AND cz.content_hash = digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256')
 WHERE NOT EXISTS (
       SELECT 1
         FROM kg.kg_edges existing
        WHERE existing.workspace_id = w.id
          AND existing.src_node_id = src.id
          AND existing.dst_node_id = dst.id
          AND existing.relation_type = COALESCE(l.relation_type::text, 'shared_tag')::kg.kg_edge_relation
          AND existing.shared_tag_label IS NOT DISTINCT FROM l.relation
 )
ON CONFLICT DO NOTHING;

WITH tags AS (
    SELECT DISTINCT w.id AS workspace_id, tag AS name
      FROM public.kg_nodes n
      JOIN public.kg_users ku ON ku.id = n.user_id
      JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
      CROSS JOIN LATERAL unnest(n.tags) AS tag
     WHERE tag IS NOT NULL AND tag <> ''
)
INSERT INTO kg.kg_nodes (workspace_id, type, canonical_name, slug, metadata)
SELECT workspace_id,
       'tag',
       name,
       lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g')),
       jsonb_build_object('source', 'legacy_tags')
  FROM tags
ON CONFLICT (workspace_key, slug) DO NOTHING;

INSERT INTO kg.chunk_node_mentions (canonical_chunk_id, kg_node_id, mention_type, metadata)
SELECT DISTINCT cc.id,
       tag_node.id,
       'tagged',
       jsonb_build_object('legacy_node_id', n.id)
  FROM public.kg_nodes n
  JOIN public.kg_users ku ON ku.id = n.user_id
  JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
  JOIN content.canonical_zettels cz
    ON cz.normalized_url = n.url
   AND cz.content_hash = digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256')
  JOIN content.canonical_chunks cc ON cc.canonical_zettel_id = cz.id
  CROSS JOIN LATERAL unnest(n.tags) AS tag
  JOIN kg.kg_nodes tag_node
    ON tag_node.workspace_id = w.id
   AND tag_node.slug = lower(regexp_replace(tag, '[^a-zA-Z0-9]+', '-', 'g'))
ON CONFLICT DO NOTHING;
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "KG backfill")
    if args.dry_run:
        print("kg backfill ready")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
