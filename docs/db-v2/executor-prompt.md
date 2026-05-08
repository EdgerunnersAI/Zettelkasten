# Executor Prompt — DB v2 Migration

You are the engine implementing the DB v2 migration. **Operate in dashboard-only mode** — output ONLY the progress bar (format: `docs/db-v2/dashboard-sample.md`, `docs/db-v2/dashboard-sample.svg`). No prose before/after.

## What this is

39-table Supabase refactor: workspace tenancy, canonical-content dedup, JWT-claim RLS, halfvec(768), scorer registry. Files:

- **Spec:** `docs/superpowers/specs/2026-05-08-db-refactor-design.md` — full DDL, 12 BLOCKER + 29 MAJOR audit fixes (§9 trace)
- **Plan:** `docs/superpowers/plans/2026-05-08-db-refactor-implementation.md` — 9 phases, ~60 tasks, TDD steps
- **Project rules:** `CLAUDE.md` — production discipline + infra guardrails. Read first.
- **Memory:** `~/.claude/projects/.../memory/project_db_refactor_decisions.md`

## Critical pitfalls

1. **JWT cast (B.1):** `core.jwt_workspace_ids()` uses `jsonb_array_elements_text`; naive `::text::uuid[]` is broken.
2. **supabase-py form (C.1):** `client.schema("content").table("canonical_zettels")`, never `client.table("content.canonical_zettels")`. Pin `supabase-py>=2.7.0`.
3. **No fictional RPCs (C.2, C.8):** `exec_sql_returning`/`execute_sql` don't exist. Use typed RPCs (`core.consume_quota`, `content.search_chunks`) or asyncpg.
4. **LISTEN needs port 5432 (C.3):** Registry adapter uses direct Postgres port, NOT pooled 6543 — pgbouncer transaction-pool drops LISTEN. Plus 60s polling fallback.
5. **HNSW after backfill (B.7):** `10_hnsw_indexes.sql` runs at cutover Step 5, AFTER rows land.
6. **Maintenance-mode (D.1):** Not implemented in Caddy today. Phase 0 Task 0.5 adds + tests.
7. **14-day legacy retention (D.2):** Old tables RENAMED `_legacy_*`, NOT dropped. Day +14 drops separately.
8. **Embedding cast (C.6):** SQL `embedding::halfvec` — do NOT re-call Gemini.
9. **Citation integrity (A.3):** Reaper SKIPS canonical rows referenced by `chat_messages.citations`.
10. **post_fork init (F.2):** RegistryAdapter starts in gunicorn `post_fork`, not FastAPI lifespan.

## Approval gates (NON-NEGOTIABLE)

STOP and ask user before:
- Each phase transition
- Provisioning paid infra (Task 0.1 Supabase v2-dev ≈ $25/mo)
- Touching any `CLAUDE.md` "Critical Infra Decision Guardrails" knob
- Cutover execution (Phase 8 go/no-go)
- Dropping `_legacy_*` tables (Day +14)
- Anything not in the written plan

Per `feedback_anything_beyond_plan_needs_approval.md`: out-of-plan = new decision = explicit chat approval first. "Execute optimally" ≠ silent scope expansion.

## Output discipline

Per `feedback_progress_bar_mode.md` + `feedback_dashboard_mode_always.md`: emit ONLY the dashboard. Tick-update in place, no narrative.

Start with Phase 0 Task 0.1: **ASK user for approval** to provision the v2-dev project.
