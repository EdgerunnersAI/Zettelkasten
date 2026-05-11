# Supabase Schema Assets

This folder owns the SQL files and schema manifests for the website's Supabase database. It is a database-assets folder, not the runtime client, deployment runner, or secret store.

## What This Folder Owns

- Versioned SQL for the active DB v2 schema under `website/_v2/`.
- SQL snapshots and migration-era files for older public-schema KG/RAG surfaces under `website/kg_public/`, `website/rag_chatbot/`, and `website/kg_features/`.
- Subsystem schema files for Nexus provider ingest, Supabase Auth integration, and summarization batch metadata.
- The DB v2 drift manifest consumed by the migration runner: `website/_v2/expected_schema.json`.

## What This Folder Does Not Own

- Runtime Supabase clients and repositories. Those live under `website/core/supabase_v2/`.
- Migration execution logic. That lives in `ops/scripts/apply_migrations.py`.
- Deployment orchestration. The deploy script and Docker image setup live under `ops/`.
- Environment files or secrets. Do not read or add `.env` files here.
- Root-level architecture documentation, product behavior, or frontend copy.

## Key Files And Subfolders

- `About.md`: this folder-scoped developer guide.
- `CLAUDE.md`: currently only a memory-context placeholder for this folder.
- `website/_v2/`: active DB v2 migration set. It defines the `core`, `content`, `kg`, `rag`, `pipelines`, and `billing` schemas; RLS policies; RPCs; HNSW indexes; billing functions; kasten-sharing policies; extraction blocklist storage; retrieval feedback events; signal materialized views; cron refresh helpers; and repeatable migrations.
- `website/_v2/repeatable/`: Flyway-style repeatable SQL files named `R__*.sql`; the migration runner reapplies them when their checksum changes.
- `website/_v2/expected_schema.json`: schema-drift manifest used by `apply_migrations.py --v2`.
- `website/kg_public/schema.sql`: legacy public-schema KG snapshot with `kg_users`, `kg_nodes`, `kg_links`, node aliases, RLS, and indexes.
- `website/kg_public/ci_baseline.sql`: CI-only legacy baseline used before replaying old forward migrations.
- `website/kg_public/migrations_archived_2026-05-11/`: archived legacy migration files. Treat these as historical unless a task explicitly asks for archive analysis.
- `website/rag_chatbot/`: legacy public-schema RAG SQL for chunks, sandboxes, chat sessions, RAG RPCs, and a variable-conflict fix.
- `website/kg_features/`: legacy KG feature migrations for embeddings/search/RPCs, global graph reads, and scale/storage optimizations.
- `website/nexus/schema.sql`: public-schema Nexus provider-ingest tables, OAuth state, ingest runs, artifacts, indexes, triggers, and RLS.
- `website/user_auth/schema.sql`: legacy Supabase Auth trigger/default-user setup for `kg_users`.
- `website/summarization_engine/001_engine_v2.sql`: summary batch run/item metadata tables and indexes.

## Entry Points And Public Interfaces

- `ops/scripts/apply_migrations.py --v2` is the main migration entry point for this tree. With `--v2`, it reads `supabase/website/_v2`, uses `SUPABASE_V2_DATABASE_URL`, runs top-level versioned SQL, then repeatable SQL, then the drift gate.
- `ops/deploy/deploy.sh` runs `apply_migrations.py --v2` during deployment before application smoke checks. It passes `SUPABASE_V2_DATABASE_URL` to the container and treats the v2 manifest as in-image state.
- `ops/Dockerfile` copies `supabase/website/_v2/` into the image because deployment runs the v2 migration set from inside the container.
- `website/core/supabase_v2/client.py` is the runtime client boundary. It reads only `SUPABASE_V2_*` settings and exposes service-role, anon, user-scoped, and database-url helpers.
- `website/core/db_version.py` gates v2 usage with `DB_SCHEMA_VERSION=v2` plus v2 Supabase configuration.
- `website/core/supabase_v2/repositories/` is the Python interface to these schemas. The repositories call schema-qualified tables and RPCs in `core`, `content`, `kg`, `rag`, `billing`, and related v2 schemas.

## Representative Runtime Flows

- Deploy migration flow: Docker image contains `_v2`; `deploy.sh` starts a one-shot container; `apply_migrations.py --v2` applies pending versioned SQL, applies changed repeatable SQL, and verifies the schema manifest unless the manifest gate is explicitly disabled.
- Zettel persistence flow: `website/core/persist.py` checks `use_supabase_v2()`, resolves the user's profile/workspace through `CoreRepository`, then writes canonical and workspace content through `ContentRepository`.
- Retrieval and Kasten flow: `RAGRepository` manages kastens, kasten membership, chat sessions/messages, chunk-share counts, and retrieval signal weights; `ContentRepository` calls content-search RPCs; `KGRepository` handles graph nodes, edges, and `kg.expand_subgraph`.
- Nexus v2 flow: Nexus service code uses `get_v2_client()` and v2 repositories/tables for provider tokens and bulk-import persistence instead of the older public-schema Nexus tables.

## Dependencies And External Contracts

- Required database extensions are declared in SQL, including `pgcrypto`, `vector`, `pg_partman`, `pg_cron`, and `pg_trgm` where needed.
- Supabase Auth JWT claims are part of the v2 access model. The v2 SQL defines helpers for workspace IDs and roles, and RLS policies use those helpers.
- Custom schemas must be exposed in Supabase API settings for PostgREST access; `_v2/08_rls_policies.sql` calls this out directly.
- Canonical content is service-role-only; authenticated reads go through scoped RPCs such as `content.search_chunks`.
- Billing schema files include Razorpay-oriented tables and functions rekeyed to `core.profiles`.
- Retrieval signal materialized views are derived from `rag.retrieval_feedback_events`; the SQL notes that materialized views do not inherit RLS, so callers must scope reads at query time.

## How To Extend Safely

- Put active schema changes in `website/_v2/`, not in legacy public-schema folders.
- Add new one-time DDL/data changes as a new numbered top-level SQL file. Do not edit a migration that has already been applied.
- Put replaceable code objects such as functions, views, RLS policy rewrites, or trigger procedures in `website/_v2/repeatable/R__*.sql` when the migration-drift runbook says they are repeatable.
- When adding a table, add explicit RLS, grants, indexes for the read/write path, and matching repository/model/test changes.
- When changing RPC signatures or return shapes, update the Python repository methods and tests that call those RPCs.
- Do not apply archived legacy migrations to the active v2 database.
- Do not add secrets, project refs, database URLs, or key material to this folder.

## Testing And Debugging Notes

- Unit coverage for the v2 schema and repositories is under `tests/unit/supabase_v2/`.
- Migration-runner behavior is covered by `tests/unit/ops/test_apply_migrations*.py` and schema-drift tests under `tests/unit/ops/`.
- Runtime v2 integration coverage is under `tests/integration/v2/`.
- RAG sandbox RPC coverage includes `tests/integration_tests/test_rag_sandbox_rpc.py`.
- Use `ops/runbooks/migration-drift.md` when the runner reports a checksum mismatch or schema drift.
- Running live migration or integration checks requires real Supabase environment variables; do not source or print `.env` files while working on this doc.

## Invariants, Gotchas, And Known Risks

- `_v2` is the active deploy path. Older public-schema directories remain in the tree for legacy reference, CI baselines, and archive analysis.
- Repeatable migrations run after top-level versioned migrations; cleanup migrations can remove old manifest rows before the repeatable file is applied.
- The default migration-drift gate is required. `MIGRATION_MANIFEST_REQUIRED=0` is an emergency fallback, not normal workflow.
- Some legacy ops scripts are annotated for future v2 porting; do not infer active runtime ownership from old imports alone.
- SQL comments in legacy files may describe historical behavior that v2 has retired. Verify against `website/_v2/`, runtime repositories, and current tests before copying any claim.

## Related Docs

- `AGENTS.md`: repository-wide operating rules, production discipline, and secret handling.
- `ops/runbooks/migration-drift.md`: checksum drift decision tree and audit-trail requirements.
- `docs/db-v2/PURGE-COMPLETE-2026-05-11.md`: DB v2 closeout state and deferred follow-ups.
- `docs/db-v2/rollback-runbook.md`: rollback paths for DB v2 incidents.
- `docs/db-v2/phase-9-pricing-enforcement-plan.md`: future pricing-enforcement work.
- `ops/.env.example`: non-secret template for required Supabase-related variable names.
