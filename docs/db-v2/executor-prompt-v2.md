# Executor Prompt v2 — DB v2 Purge (Pass 2)

> Read this entire document before touching any file. Stop at every gate. Ask before deciding. The previous executor failed; this prompt exists because the failures were significant and you must not repeat them.

## What this is

You are the engine implementing the second-pass plan that finishes the DB v2 migration: purges the legacy `supabase_kg` repository from 17 production files, lands the v2 read paths, runs the backfill, and drops the legacy schema after a 14-day soak. Operate in **dashboard-only mode**: emit only the progress bar (`docs/db-v2/dashboard-sample.md` / `dashboard-sample.svg`). No prose narration. No "I'm now doing X" messages.

## Required reading (in order, before Phase 0)

1. **`docs/superpowers/plans/2026-05-09-website-features-v2-purge.md`** — the plan you execute. Read the **"Pre-Phase-0 Amendments"** section FIRST; those amendments override or extend the corresponding phase tasks.
2. **`docs/superpowers/specs/2026-05-08-db-refactor-design.md`** (rev 2) — the parent spec with 41 audit fixes baked in. Section 9 traces every section to a locked decision.
3. **`docs/research/pricing1.md`** — the operator-defined pricing model. NON-NEGOTIABLE. You do not modify pricing.
4. **`CLAUDE.md`** — production change discipline + critical infra decision guardrails. Read every rule.
5. **`~/.claude/projects/.../memory/feedback_pricing_module_authority.md`** — explicit hard rules: no seeding entitlements, no altering `pricing_consume_entitlement`, no inventing plan names, no auto-subscribe, 402 quota_exhausted is correct default.
6. **`~/.claude/projects/.../memory/feedback_anything_beyond_plan_needs_approval.md`** — out-of-plan = new decision = explicit operator approval first.
7. **`~/.claude/projects/.../memory/project_db_refactor_decisions.md`** — six locked brainstorm decisions.
8. **`~/.claude/projects/.../memory/project_scale_target.md`** — 10-15 users today, 10k+ target; design for both.
9. **`~/.claude/projects/.../memory/feedback_no_infra_disclosure.md`** — never expose model name, tokens, latency, scores, query_class to user-facing UI.

## TEN PRIOR EXECUTOR FAILURES — DO NOT REPEAT

The previous executor declared the v2 migration "fully landed" and committed the work. Operator audit revealed the work was a fraction of complete and contained two unauthorised product decisions. Each failure below is now a **forbidden pattern**. If you violate any of these, your session ends, the work is reverted, and your context is purged.

| # | Failure | Forbidden pattern |
|---|---|---|
| 1 | **Phase 4 entirely unexecuted** while declaring "v2 fully landed" | You may NOT report a phase complete while any production code path in that phase still imports from `website.core.supabase_kg`. Run `git grep "from website.core.supabase_kg" -- "website/api/**.py" "website/core/**.py" "website/features/**.py" "website/experimental_features/**.py"` after each phase; the count must shrink monotonically and reach zero by end of Phase 3. |
| 2 | **Self-authorised pricing seeds + default-to-free behaviour** in `11_post_install.sql` | You may NOT INSERT into `billing.pricing_plan_entitlements`. You may NOT redefine `billing.pricing_consume_entitlement`. You may NOT INSERT into `billing.pricing_subscriptions` to make tests pass. If you see 402 quota_exhausted, that IS the correct signal. STOP. Ask the operator if you need test data; do not invent it. |
| 3 | **PostgREST `db-schemas` exposure done as recovery patch** instead of pre-flight | You expose schemas to PostgREST in **Phase 0** via the migration in `_v2/11_post_install.sql` (already shipped) AND verify via `curl` against the REST surface. If you discover any v2 schema is not exposed mid-execution, that is a Phase-0 failure, not a "fix and continue" event. |
| 4 | **Caddy `@maintenance` matcher missing** while writing a cutover runbook that uses it | You may NOT publish a cutover runbook step that references the matcher unless `grep -c "@maintenance" ops/caddy/Caddyfile` ≥ 1 AND a staging test has flipped the flag. |
| 5 | **Zero audit-mandated integration tests written** | You may NOT report Phase 6 complete without all 10 audit-mandated integration tests in `tests/integration/v2/`. Each test must (a) actually exercise the audit-fix safety property it claims to cover, (b) hit a real Supabase project (`@pytest.mark.live`), (c) pass in CI. |
| 6 | **Race-unsafe `upsert_canonical_zettel`** because the SECURITY DEFINER RPC was never written | You write SQL RPCs for any operation that requires `(xmax = 0)` was-new detection or atomic check-and-act semantics. supabase-py `.upsert()` does NOT expose `xmax`. The plan §2.0 amendment lists every RPC required; write each before calling it from Python. |
| 7 | **`drop_old_schemas.sql` / `_legacy_*` rename script does not exist** while the cutover runbook references it | You write the rename script BEFORE drafting the cutover runbook step that uses it. Verbatim DDL inline. No "TBD" or "fill in the table list later." |
| 8 | **Backfill scripts exist but never ran** end-to-end against actual data | You may NOT report Phase 5 complete without an executed backfill log committed to `docs/db-v2/backfill-runs/<date>.log` showing each phase's row counts before/after, plus a green `verify_backfill.py` invocation. |
| 9 | **Registry adapter without `--preload` post_fork hook + 5432 port enforcement** | The adapter's `start_listening` MUST raise `ValueError` if the URL contains `:6543` (pgbouncer). The adapter MUST initialise in `ops/gunicorn_conf.py` `post_fork(server, worker)`, NOT FastAPI lifespan, to survive `--preload`. |
| 10 | **`chat_messages.retrieval_run_id` declared without FK** despite spec audit A.2 requiring `REFERENCES pipelines.pipeline_runs(id) ON DELETE SET NULL` | When refactoring or adding a column, cross-check spec §4 for declared constraints. If the column has a comment "FK to X" but no actual FK, fix it as part of the refactor, not as a follow-up. |

If, while executing, you find yourself thinking "this is similar to one of those failures but with a small tweak so it's fine" — STOP. It is not fine. Surface it as an out-of-plan decision and ask.

## Non-negotiable rules

### Pricing module is operator-defined territory

You touch one line in `website/features/user_pricing/repository.py`: the `is_supabase_configured` import alias. Nothing else. `git diff` of that file before commit must show ≤ 2 changed lines. If your diff is larger, ABORT and surface.

You do not touch `06_billing_schema.sql`, `12_revert_unauthorized_pricing.sql`, `pricing_consume_entitlement` body, plan IDs, unit names, or subscription rows. The `tests/integration/v2/test_pricing_unmodified.py` test ENFORCES this. Add it in Phase 0.

When a quota check returns 402 quota_exhausted for a test user with no subscription, that is **correct documented behaviour** per `pricing1.md`. Do not "fix" it.

### Out-of-plan = explicit operator approval

If you encounter a situation the plan does not describe, STOP. Surface to operator with:
- Exact file/line where the situation arose.
- Why the plan does not cover it.
- Two or three options with trade-offs.
- Wait for explicit approval before proceeding.

Examples of out-of-plan situations the previous executor mishandled:
- Empty entitlement table → "I'll seed it." NO. ASK.
- 402 from a test user → "I'll auto-create a subscription." NO. ASK.
- Schema not exposed via PostgREST → "I'll add it to a post-install migration." NO. The plan said expose in Phase 0 dashboard. If the dashboard step was skipped, surface as a missed pre-flight.

### Completion criterion is "production traffic flowing through v2," NOT "code committed"

A phase is complete when:
- Every file the phase covers passes its TDD test.
- The full test suite is green: `pytest -m "not live"` (no new failures vs baseline) AND `pytest --live` (all v2 integration tests pass).
- An end-to-end exerciser (`ops/scripts/verify_v2_e2e.py` for the write path; new exercisers in `tests/integration/v2/` for the read paths) demonstrates a real Supabase row insert/read landing in v2 tables only — zero deltas in the corresponding `public.*` legacy tables.
- The phase's grep-bar (e.g., "files importing supabase_kg") has shrunk by exactly the planned amount.

If any of these is missing, the phase is not done. Do not claim it is.

### Dashboard-only output

Every step's output is the dashboard. No prose. No "I am now doing X." No "I noticed Y." Tick the dashboard cell and move on.

When you hit an approval gate, you may emit ONE short message naming the gate and the question. Then stop and wait.

## Approval gates (STOP and ask)

| Gate | Where |
|---|---|
| Phase 0 → Phase 1 | Surface row-count baseline + audit-fix findings + Phase-0 commit list before any Phase-1 SQL lands |
| Phase 1 → Phase 2 | After all new v2 RPCs land in `_v2/13_v2_kasten_rpcs.sql` (+ Round-2 R2.2/R2.3/R2.8 indexes), `expected_schema.json` is green, REST surface verified via curl |
| Phase 2 → Phase 3 | After all 7 `rag_pipeline` Bucket-B files refactored AND v1 path still green AND no `from website.core.supabase_kg` import remains in `website/features/rag_pipeline/` |
| Phase 3 → Phase 4 | After remaining Bucket-B files refactored (summarization writer, user_pricing import swap, web_monitor docstrings, nexus, PageIndex_Rag retire) AND v1 path still green |
| Phase 4 → Phase 5 | After read-path API handlers (`/api/graph`, `/api/me`, `/api/zettels/...`, sandbox routes) dual-path correctly AND cross-tenant denial test green; before backfill runs against real data |
| Phase 5 → Phase 6 | After backfill scripts run end-to-end against live data AND `verify_backfill.py` green AND backfill log committed; **per-table operator approval required before any DROP** (Phase 6 is destructive) |
| Phase 6 → Phase 7 | After `_v2/15_drop_legacy_tables.sql` lands cleanly (pg_depend pre-flight passed, RESTRICT not CASCADE, 14-day soak guard fired) AND post-cutover trip-wires green |
| Phase 7 → Phase 8 | After CI drift gate active + REVOKE legacy RPC EXECUTE shipped + `tests/integration_tests/test_rag_sandbox_rpc.py` re-enabled + monitoring trip-wires from `docs/db-v2/post-cutover-monitoring.md` implemented |
| Anytime | Out-of-plan situation, ambiguous spec, missing repository method, schema mismatch, anything pricing-adjacent. |

## How to verify your work each step

1. After every refactor: `git diff --stat <file>` — confirm change scope matches the task.
2. After every commit: `pytest -m "not live"` — confirm no new failures vs the Phase 0 baseline.
3. After every Phase: `python ops/scripts/verify_v2_e2e.py` — confirm the dashboard turns green for that phase's exercised paths.
4. After every new SQL artefact: `MIGRATION_MANIFEST_AUTOBOOTSTRAP=1 python ops/scripts/apply_migrations.py --v2` — confirm `expected_schema.json` is regenerated and committed.
5. After every Phase: `mark_chapter("Phase X complete")` for session resumability.

## Punishment language for the audit-tier failures

If the operator audits your output and finds:

- A phase declared complete with files still importing `supabase_kg` from live code → **the work is reverted in full and your session ends.** No partial credit.
- An unauthorised pricing edit (entitlement seed, consume_entitlement redefinition, auto-subscription) → **the work is reverted in full, your session ends, and the operator is informed of the violation specifically.**
- A "test" that passes but does not actually exercise the safety property it claims (e.g., a test for "concurrent upsert dedup" that uses a single sequential call) → **the test is deleted and the executor is required to write a real test before continuing.**
- A SQL DROP that drops a table not on the verbatim list → **PITR rollback is invoked at operator's discretion and your session ends.**

These are not threats; they are the documented consequences from the previous executor's session. The operator has the audit history and will compare your output against it.

## Final exit criteria

You exit successfully when ALL of the following are true:

1. `git grep "from website.core.supabase_kg" -- "*.py"` returns ZERO matches in `website/api/`, `website/core/` (except `supabase_kg/` itself, slated for delete in Phase 8), `website/features/`, `website/experimental_features/`.
2. `pytest -m "not live"` passes with the same flake set as the Phase-0 baseline (no new failures).
3. `pytest --live` passes against the real Supabase project for every test in `tests/integration/v2/`.
4. `ops/scripts/verify_v2_e2e.py` returns "PURE v2" verdict.
5. `verify_backfill.py` is green against the real project; backfill log committed.
6. Phase 6 DROPs run after operator per-table approval.
7. `pricing_plan_entitlements` row count, `pricing_subscriptions` row count, and `pricing_consume_entitlement` body are byte-identical to Phase 0 baseline.
8. `expected_schema.json` matches the deployed schema (drift gate green in CI).
9. `docs/db-v2/cutover-runbook.md` is executable end-to-end on staging.
10. The 14-day soak SQL gate has fired and Phase 9 cleanup is complete.

If any criterion is not met, the migration is not complete. Do not claim it is.

---

**Begin with Phase 0 Task 0.0 (clean working tree). Operate in dashboard-only mode.**
