# DB v2 Cutover Runbook

## Preconditions

- `.env.v2` contains `SUPABASE_V2_URL`, `SUPABASE_V2_ANON_KEY`, `SUPABASE_V2_SERVICE_ROLE_KEY`, `SUPABASE_V2_DATABASE_URL`, and `SUPABASE_V2_LISTEN_DATABASE_URL`.
- Supabase v2 API exposed schemas include `core, content, kg, rag, pipelines, billing`.
- Production PITR is enabled and a restore drill has been completed.
- Caddy maintenance mode has been tested.
- `python -m pytest tests/unit/ops/test_apply_migrations.py tests/unit/ops/test_apply_migrations_dsn.py tests/unit/ops/test_apply_migrations_v2.py tests/unit/supabase_v2 -q` passes.

## Steps

1. Enable maintenance mode.
2. Take/confirm Supabase PITR baseline timestamp.
3. Apply DB v2 files 00-09:
   `python ops/scripts/apply_migrations.py --v2 --target=v2-dev --update-manifest`
4. Run pg_partman maintenance if needed.
5. Run backfill with gates:
   `python ops/scripts/refactor_v2/00_full_backfill.py --continue`
6. Verify backfill:
   `python ops/scripts/refactor_v2/verify_backfill.py --continue`
7. Apply HNSW after rows land:
   `psql "$SUPABASE_V2_DATABASE_URL" -f supabase/website/_v2/10_hnsw_indexes.sql`
8. Set app env `DB_SCHEMA_VERSION=v2` and deploy.
9. Warm `/api/health/warm`.
10. Smoke test summarize, graph, pricing, and RAG chat.
11. Disable maintenance mode.

## Go/No-Go Checks

- `content.canonical_zettels` count is non-zero.
- `content.canonical_chunks` count is non-zero.
- `content.workspace_chunk_membership` has no orphan chunk/workspace references.
- `rag.retrieval_pipeline_config` has all required scorers for `prod`.
- `content.search_chunks` succeeds for an authorized JWT and fails for unauthorized workspace.
- Direct SELECT from canonical chunks is denied to authenticated clients.

