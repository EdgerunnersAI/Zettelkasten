# Migration CI architecture — research consolidation (2026-05-12)

**Status:** Operator-approved (Option A, 2026-05-12). Implemented in commit on
hardening-sprint after Iter 7 of the Fresh-Supabase gate spiraled into a
warn-only-on-drift + auto-commit-from-CI loop that violated industry
consensus.

## Problem

7 consecutive CI iterations on the Fresh-Supabase _v2 apply + manifest check
job, each patching one symptom of a deeper architectural mismatch:

- The job applied `_v2/*.sql` migrations to a fresh local Supabase stack,
  then compared the resulting schema against a hand-committed
  `supabase/website/_v2/expected_schema.json` snapshot.
- The snapshot was captured pre-Phase-8 and contains 30+ legacy
  `public.kg_*` / `public.rag_*` function references.
- Phase 8 migrations correctly dropped those references.
- Fresh-apply therefore produces a CLEANER schema than the snapshot expects.
- Each "fix" (skip migration 15, auto-regen + auto-commit manifest in CI,
  warn-only-on-drift) addressed one layer but exposed the next.

## Research methodology

Three independent subagents dispatched in parallel, then cross-checked
before drawing conclusions (per CLAUDE.md Research Discipline).

1. Industry-standard PR-CI migration gates at Stripe, GitHub, Shopify,
   DoorDash, Atlassian, Notion.
2. Canonical "stale snapshot + drop migration" escape patterns in
   Flyway / Liquibase / Atlas / Sqitch / Bytebase.
3. Supabase-specific PR-CI patterns (official docs + 2024–2026 community).

## Universal consensus (3/3 agents agree)

PR CI should gate on:

1. **Migrations apply cleanly** (`exit 0` from the apply runner).
2. **Static lint** for destructive / lock-prone ops
   ([Atlas migrate lint](https://atlasgo.io/versioned/lint), Squawk,
   Bytebase rules).
3. **Integration tests** against the freshly-applied schema
   ([Supabase pgTAP testing overview](https://supabase.com/docs/guides/local-development/testing/overview)).

PR CI should NOT:

- Compare fresh-apply output to a committed schema snapshot — that's an
  anti-pattern in 2024–2026. Atlas, Flyway, Liquibase, Bytebase, and
  Supabase official docs all converge.
- Auto-regenerate and auto-commit snapshots from CI — Atlas's drift recipe
  is explicitly "check that no files were generated; if files appear, the
  build fails so the developer regenerates locally"
  ([Detect Migrations Drift in CI](https://atlasgo.io/faq/desired-state-drift)).
  Bytebase frames drift as a human decision (Baseline vs Revert), never
  automation ([Schema Drift Detection](https://docs.bytebase.com/change-database/drift-detection)).
  Redgate warns leaving `baselineOnMigrate=true` past initial cutover
  masks real drift and corrupts later migrations
  ([Flyway Baseline On Migrate](https://documentation.red-gate.com/fd/flyway-baseline-on-migrate-setting-277578974.html)).

Snapshot equality belongs in **nightly drift-against-prod** jobs, not
per-PR gates.

## Sources (consolidated, all <5 years old)

- [Atlas — Verifying Migration Safety (2024)](https://atlasgo.io/versioned/lint)
- [Atlas — Schema Drift Detection (2024)](https://atlasgo.io/monitoring/drift-detection)
- [Atlas — Detect Migrations Drift in CI (2024)](https://atlasgo.io/faq/desired-state-drift)
- [Atlas — Applying Schema Migrations (2024)](https://atlasgo.io/versioned/apply)
- [Supabase — Testing Overview (2025)](https://supabase.com/docs/guides/local-development/testing/overview)
- [Supabase — Database Migrations (2024)](https://supabase.com/docs/guides/deployment/database-migrations)
- [Supabase — Vibe Coder's Guide to Environments (2025)](https://supabase.com/blog/the-vibe-coders-guide-to-supabase-environments)
- [Supabase Discussion #37503 — Professional standard (2024)](https://github.com/orgs/supabase/discussions/37503)
- [Supabase Discussion #18483 — Sync local and prod (2024)](https://github.com/orgs/supabase/discussions/18483)
- [Bytebase — CI/CD Pipeline for DB Schema Migration (2024)](https://www.bytebase.com/blog/how-to-build-cicd-pipeline-for-database-schema-migration/)
- [Bytebase — Schema Drift Detection (2024)](https://docs.bytebase.com/change-database/drift-detection)
- [Bytebase — What is Database Schema Drift? (2023)](https://www.bytebase.com/blog/what-is-database-schema-drift/)
- [Flyway — Squashing Migrations (2022)](https://medium.com/att-israel/flyway-squashing-migrations-2993d75dae96)
- [Redgate — Flyway Baselines and Consolidations (2024)](https://www.red-gate.com/hub/product-learning/flyway/flyway-baselines-and-consolidations)
- [Redgate — Flyway Baseline On Migrate (2024)](https://documentation.red-gate.com/fd/flyway-baseline-on-migrate-setting-277578974.html)
- [Liquibase — Flattening your changelog (2021)](https://medium.com/@Rogier.Slag/flattening-your-liquibase-changelog-cb0b5460a74f)
- [Liquibase — changelog-sync command (2024)](https://docs.liquibase.com/commands/utility/changelog-sync.html)
- [Stripe — Online migrations at scale (2017, still canonical)](https://stripe.com/blog/online-migrations)
- [Pragmatic Engineer — Real-World Engineering Challenges #6: Migrations (2023)](https://newsletter.pragmaticengineer.com/p/real-world-engineering-challenges)
- [Finalist Tech — Deploying Supabase Migrations with GitHub Actions (2024)](https://techblog.finalist.nl/blog/deploying-supabase-migrations-github-actions)
- [Practical checks for alembic migrations (2023)](https://ldirer.com/blog/posts/practical-checks-alembic-migrations)

## Decision

**Option A applied (operator-approved 2026-05-12).** Delete the snapshot
comparison gate from `migration-ci.yml` Fresh-Supabase job:

1. Drop the **Regenerate manifest + auto-commit on drift** step (CI
   auto-commit = explicit anti-pattern per Atlas/Bytebase/Redgate).
2. Drop the **Verify manifest matches freshly-applied DB** step (wrong
   gate for PR CI per all 3 research agents).
3. Apply step alone gates on `exit 0` from `apply_migrations.py --v2`.
4. `MIGRATION_MANIFEST_REQUIRED=0` retained on the apply step's env so
   the script's inline verify-after-apply (when manifest exists) is
   warn-only — this is the documented escape (apply_migrations.py:862).
5. Permissions reverted from `contents: write` → `contents: read` since
   nothing pushes back to the branch anymore.

## What this PR CI gate now answers

| Question | Gated by | Where |
|---|---|---|
| Do `_v2/*.sql` migrations apply without error? | `apply_migrations.py` rc=0 | This job |
| Any destructive/lock-prone operations? | Squawk static lint | `lint-v2-sql` job (same file) |
| Does the schema function correctly? | Integration tests | `pytest` workflows |
| Has production drifted from repo? | Schema-snapshot diff | Nightly `v2_drift_check.yml` |

## Follow-up (deferred, not blocking)

`supabase/website/_v2/expected_schema.json` still contains pre-Phase-8
legacy `public.*` references. This does NOT block the PR CI merge (the
gate is gone). It WILL cause `v2_drift_check.yml` to flag stale entries
on its next nightly run until regenerated.

Recommended regeneration path (operator decision, separate ticket):

1. Run `supabase start` locally + `apply_migrations.py --v2 --update-manifest`
   against fresh state. Single committed baseline.
2. Or trigger a manual `workflow_dispatch` of `migration-ci.yml` with a
   one-shot `--update-manifest` flag (new branch, regenerate, commit,
   merge — single-PR baseline pattern).

This matches the Flyway CDRB / Atlas baseline / Liquibase flatten +
changelog-sync canonical "escape the spiral" pattern: human ships
baseline + snapshot atomically in one reviewed commit.

## What we explicitly do NOT do (anti-patterns avoided)

- Auto-regenerate and auto-commit snapshots from CI
  (Atlas/Bytebase/Redgate explicitly warn against this).
- Gate PR merge on snapshot equality
  (Supabase docs, Atlas, Flyway, Liquibase, Bytebase all gate on
  apply + lint + tests instead).
- Leave `MIGRATION_MANIFEST_REQUIRED=0` set in production deploy paths
  (it's PR-CI-only; nightly drift check and prod deploy both run with
  the default `=1` so drift is hard-fail there).
