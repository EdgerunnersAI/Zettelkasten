# Execution Dashboard — Format Specification

The executor outputs ONLY this dashboard, tick-updated in place. Zero prose before/after.

## Format

```
═══════════════════════════════════════════════════════════════════════
  DB REFACTOR v2 — EXECUTION DASHBOARD                      T+02:14:33
═══════════════════════════════════════════════════════════════════════

PHASE PROGRESS
  Phase 0  Pre-flight                       [██████████] 9/9   ✓
  Phase 1  Schema DDL (idempotent dev)      [██████░░░░] 7/12  ●
  Phase 2  Python repository layer          [░░░░░░░░░░] 0/11
  Phase 3  Scorer registry adapter (MAND.)  [░░░░░░░░░░] 0/4
  Phase 4  API + retrieval updates          [░░░░░░░░░░] 0/6
  Phase 5  Backfill scripts (gated)         [░░░░░░░░░░] 0/10
  Phase 6  Test coverage                    [░░░░░░░░░░] 0/8
  Phase 7  Cutover runbooks                 [░░░░░░░░░░] 0/3
  Phase 8  Cutover execution                [░░░░░░░░░░] 0/2
  Phase 9  Post-cutover cleanup             [░░░░░░░░░░] 0/3

CURRENT TASK
  ► Task 1.7: Apply 06_billing_schema.sql (rekeyed pricing tables)
  ► Step 4: psql apply against v2-dev
  ► Started T+02:11:45 ago

LAST 3 COMMITS (master)
  7d77323  docs: db refactor implementation plan
  1218997  docs: db refactor design spec
  d6a101f  feat: iter-12 decision gate plus rollback addendum

BLOCKED / WAITING
  (none)

NEXT APPROVAL GATE
  → After Phase 1 completion (12/12 tasks): present DDL diff for user review
    before starting Phase 2

AUDIT-FIX VERIFICATION (12 BLOCKER + 29 MAJOR)
  ✓ B.1  jwt_workspace_ids() cast            [test_jwt_workspace_ids]
  ✓ B.2  kg_nodes UNIQUE via gen-col         [DDL applied 1.4]
  ✓ B.3  expand_subgraph auth check          [DDL applied 1.4]
  ✓ B.4  soft-delete trigger split           [DDL applied 1.3]
  ●  B.5  search_chunks RPC                   [DDL applied; test pending 6.0]
  ✓ B.6  workspace_chunk_membership PK       [DDL applied 1.3]
  ●  B.7  HNSW post-backfill                  [10_hnsw_indexes.sql in repo]
  ✓ C.1  supabase-py .schema(...).table(...) [pinned 2.7.0; used in 2.4]
  ●  C.2  consume_quota typed RPC             [DDL applied; test pending 2.10]
  ●  C.3  LISTEN port 5432 + 60s poll         [pending Phase 3]
  ─  rest                                     [scheduled]

NOTES
  (none)
═══════════════════════════════════════════════════════════════════════
```

## Symbol legend

- `✓` — completed and verified
- `●` — in progress
- `─` — scheduled (not yet started)
- `✗` — failed (requires user attention; escalates)
- `[████░░░░] 4/8` — progress bar; filled blocks = completed steps; trailing count

## Update cadence

- After every step completion: tick the appropriate cell, no other output
- After every phase transition: STOP, surface a transition summary, wait for user approval gate
- After every commit: update "LAST 3 COMMITS" section
- After every audit-fix landing: tick the relevant audit row

## Where the SVG fits

The companion `dashboard-sample.svg` is a visual reference image showing what the rendered dashboard should look like for a presentation/screenshot. The text format above is what the executor literally outputs.
