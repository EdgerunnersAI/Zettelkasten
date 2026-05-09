"""Backfill content.canonical_* and content.workspace_* rows for DB v2."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import run_statements, load_config, parse_args, require_continue, run_async  # noqa: E402


SQL = """
DO $$
BEGIN
    IF to_regclass('public.kg_nodes') IS NULL THEN
        RAISE NOTICE 'legacy public.kg_nodes table is absent; skipping content backfill';
        RETURN;
    END IF;
END $$;

WITH legacy AS (
    SELECT n.*,
           digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256') AS zettel_hash
      FROM public.kg_nodes n
)
INSERT INTO content.canonical_zettels (
    normalized_url, content_hash, source_type, title, body_md, publication_date, source_metadata, created_at
)
SELECT DISTINCT ON (normalized_url, content_hash)
       url AS normalized_url,
       zettel_hash AS content_hash,
       source_type,
       name AS title,
       summary AS body_md,
       node_date AS publication_date,
       jsonb_build_object(
           'legacy_node_id', id,
           'legacy_user_id', user_id,
           'metadata', metadata,
           'aliases', aliases,
           'summary_hash', summary_hash
       ) AS source_metadata,
       created_at
  FROM legacy
 WHERE url IS NOT NULL
 ORDER BY normalized_url, content_hash, created_at
ON CONFLICT (normalized_url, content_hash) DO NOTHING;

WITH mapped AS (
    SELECT c.id AS canonical_zettel_id,
           ch.chunk_idx,
           ch.content,
           ch.content_hash,
           ch.chunk_type,
           ch.start_offset,
           ch.end_offset,
           ch.token_count,
           ch.embedding,
           jsonb_strip_nulls(ch.metadata || jsonb_build_object(
               'legacy_chunk_id', ch.id,
               'legacy_node_id', ch.node_id,
               'metadata_enriched_at', ch.metadata_enriched_at
           )) AS metadata,
           ch.created_at
      FROM public.kg_node_chunks ch
      JOIN public.kg_nodes n ON n.user_id = ch.user_id AND n.id = ch.node_id
      JOIN content.canonical_zettels c
        ON c.normalized_url = n.url
       AND c.content_hash = digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256')
)
INSERT INTO content.canonical_chunks (
    canonical_zettel_id, chunk_idx, content, content_hash, chunk_type,
    start_offset, end_offset, token_count, embedding, metadata, created_at
)
SELECT canonical_zettel_id,
       chunk_idx,
       content,
       content_hash,
       chunk_type,
       start_offset,
       end_offset,
       token_count,
       embedding::halfvec,
       metadata,
       created_at
  FROM mapped
ON CONFLICT (canonical_zettel_id, chunk_idx) DO NOTHING;

WITH atomic_nodes AS (
    SELECT c.id AS canonical_zettel_id,
           0 AS chunk_idx,
           coalesce(nullif(n.summary, ''), n.name, n.url) AS content,
           digest(coalesce(nullif(n.summary, ''), n.name, n.url), 'sha256') AS content_hash,
           'atomic'::text AS chunk_type,
           jsonb_build_object('legacy_node_id', n.id, 'fallback_atomic', true) AS metadata,
           n.created_at
      FROM public.kg_nodes n
      JOIN content.canonical_zettels c
        ON c.normalized_url = n.url
       AND c.content_hash = digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256')
     WHERE NOT EXISTS (
           SELECT 1
             FROM public.kg_node_chunks ch
            WHERE ch.user_id = n.user_id AND ch.node_id = n.id
     )
       AND coalesce(nullif(n.summary, ''), n.name, n.url) IS NOT NULL
)
INSERT INTO content.canonical_chunks (
    canonical_zettel_id, chunk_idx, content, content_hash, chunk_type, metadata, created_at
)
SELECT canonical_zettel_id, chunk_idx, content, content_hash, chunk_type, metadata, created_at
  FROM atomic_nodes
ON CONFLICT (canonical_zettel_id, chunk_idx) DO NOTHING;

WITH mapped AS (
    SELECT w.id AS workspace_id,
           c.id AS canonical_zettel_id,
           n.summary,
           n.engine_version,
           n.tags,
           n.created_at,
           n.updated_at
      FROM public.kg_nodes n
      JOIN public.kg_users ku ON ku.id = n.user_id
      JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
      JOIN content.canonical_zettels c
        ON c.normalized_url = n.url
       AND c.content_hash = digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256')
)
INSERT INTO content.workspace_zettels (
    workspace_id, canonical_zettel_id, ai_summary, ai_summary_engine_version,
    user_tags, added_via, created_at, updated_at
)
SELECT workspace_id, canonical_zettel_id, summary, engine_version, tags, 'migration', created_at, updated_at
  FROM mapped
ON CONFLICT (workspace_id, canonical_zettel_id) DO UPDATE
SET ai_summary = EXCLUDED.ai_summary,
    ai_summary_engine_version = EXCLUDED.ai_summary_engine_version,
    user_tags = EXCLUDED.user_tags,
    updated_at = now();

INSERT INTO content.workspace_chunk_membership (
    workspace_id, canonical_chunk_id, workspace_zettel_id
)
SELECT wz.workspace_id, cc.id, wz.id
  FROM content.workspace_zettels wz
  JOIN content.canonical_chunks cc ON cc.canonical_zettel_id = wz.canonical_zettel_id
ON CONFLICT DO NOTHING;
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "canonical content backfill")
    if args.dry_run:
        print("canonical content backfill ready; embeddings use SQL embedding::halfvec casts and never re-call Gemini")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
