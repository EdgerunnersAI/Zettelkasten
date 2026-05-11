# Migration drift runbook

## When this fires

`apply_migrations.py` halts with `DRIFT DETECTED — checksum mismatch for <file>` (exit code 3).
Means the migration file was edited after being applied to production.

## Decision tree

1. Was the edit intentional?
   - No (rebase artifact, merge conflict, accidental touch): option [a] revert.
   - Yes (you genuinely changed the migration body): continue.

2. Is the object a function / view / RLS policy / trigger procedure (i.e., a code-object using CREATE OR REPLACE)?
   - Yes: move file to `supabase/website/_v2/repeatable/R__<name>.sql`. Add a one-shot manifest cleanup migration to DELETE the old manifest row keyed by the old filename. The repeatable runner re-applies on hash change.
   - No (table / column / constraint / data migration): create a NEW versioned migration. Don't edit applied files.

## Why halt rather than auto-reconcile

Per Charity Majors (Honeycomb 2024-2026), Google SRE Workbook ch. 16, and DORA 2024: auto-reconciliation hides real schema drift and masks recurring failures. Industry-standard for schema migrations is operator-in-loop. Atlas, Flyway, Liquibase, golang-migrate all halt by default. We follow the same convention.

## Audit trail

Every DB-direct touch (any manual UPDATE on core._migrations_applied or other state tables) must be logged in `ops/runbooks/db-direct-touch.log`. One entry per touch:

```
ISO_TIMESTAMP | operator | target_table | statement | before | after | reason | ticket_or_PR
```

If the log grows >3 entries in a month, the migration workflow has a real bug to fix.

NIST SP 800-53 AU-2/AU-12, SOC 2 CC7.2 compliance basis.
