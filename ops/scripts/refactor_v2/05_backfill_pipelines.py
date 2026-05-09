"""Backfill pipeline run audit rows for DB v2."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import run_statements, load_config, parse_args, require_continue, run_async  # noqa: E402


SQL = """
INSERT INTO pipelines.pipeline_runs (
    kind, status, metrics, started_at, finished_at, created_at
)
SELECT 'rag_ingest',
       'succeeded',
       jsonb_build_object(
           'legacy_kg_nodes', (SELECT COUNT(*) FROM public.kg_nodes),
           'workspace_zettels', (SELECT COUNT(*) FROM content.workspace_zettels),
           'canonical_chunks', (SELECT COUNT(*) FROM content.canonical_chunks)
       ),
       now(),
       now(),
       now()
WHERE to_regclass('public.kg_nodes') IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
        FROM pipelines.pipeline_runs
       WHERE kind = 'rag_ingest'
         AND metrics ? 'legacy_kg_nodes'
  );

INSERT INTO pipelines.pipeline_runs (
    workspace_id, kind, status, metrics, error, started_at, finished_at, created_at
)
SELECT NULL,
       'recompute_signals',
       CASE WHEN status = 'success' THEN 'succeeded' ELSE 'failed' END,
       jsonb_build_object('rows_inserted', rows_inserted, 'rows_aggregated', rows_aggregated, 'legacy_run_id', id),
       error_message,
       ran_at,
       ran_at,
       ran_at
  FROM public.recompute_runs
 WHERE to_regclass('public.recompute_runs') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
         FROM pipelines.pipeline_runs pr
        WHERE pr.kind = 'recompute_signals'
          AND pr.metrics ->> 'legacy_run_id' = public.recompute_runs.id::text
   );
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "pipeline backfill")
    if args.dry_run:
        print("pipeline backfill ready")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
