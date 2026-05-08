"""Recompute DB v2 chunk-level retrieval signal weights from legacy KG usage edges."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import run_statements, load_config, parse_args, require_continue, run_async


SQL = """
WITH mapped AS (
    SELECT w.id AS workspace_id,
           src_cc.id AS source_chunk_id,
           dst_cc.id AS target_chunk_id,
           e.query_class,
           sum(e.delta * exp(-EXTRACT(epoch FROM (now() - e.created_at)) / 2592000.0)) AS weight
      FROM public.kg_usage_edges e
      JOIN public.kg_users ku ON ku.id = e.user_id
      JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
      JOIN public.kg_nodes src_n ON src_n.user_id = e.user_id AND src_n.id = e.source_node_id
      JOIN public.kg_nodes dst_n ON dst_n.user_id = e.user_id AND dst_n.id = e.target_node_id
      JOIN content.canonical_zettels src_cz
        ON src_cz.normalized_url = src_n.url
       AND src_cz.content_hash = digest(coalesce(src_n.url, '') || E'\n' || coalesce(src_n.summary, ''), 'sha256')
      JOIN content.canonical_zettels dst_cz
        ON dst_cz.normalized_url = dst_n.url
       AND dst_cz.content_hash = digest(coalesce(dst_n.url, '') || E'\n' || coalesce(dst_n.summary, ''), 'sha256')
      JOIN LATERAL (
          SELECT id FROM content.canonical_chunks
           WHERE canonical_zettel_id = src_cz.id
           ORDER BY chunk_idx
           LIMIT 1
      ) src_cc ON true
      JOIN LATERAL (
          SELECT id FROM content.canonical_chunks
           WHERE canonical_zettel_id = dst_cz.id
           ORDER BY chunk_idx
           LIMIT 1
      ) dst_cc ON true
     GROUP BY w.id, src_cc.id, dst_cc.id, e.query_class
)
INSERT INTO rag.retrieval_signal_weights (
    workspace_id, source_canonical_chunk_id, target_canonical_chunk_id,
    query_class, weight, refreshed_at
)
SELECT workspace_id, source_chunk_id, target_chunk_id, query_class, weight, now()
  FROM mapped
ON CONFLICT (workspace_id, source_canonical_chunk_id, target_canonical_chunk_id, query_class)
DO UPDATE SET weight = EXCLUDED.weight, refreshed_at = now();
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "signal weight recompute")
    if args.dry_run:
        print("signal weight recompute ready")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
