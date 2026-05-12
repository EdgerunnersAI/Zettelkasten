# Squawk Migration Lint Guide

**Date introduced:** 2026-05-12 (WAVE-D hardening, H-2)
**Status:** Authoritative gate for new migrations; legacy `_v2/*.sql` fenced off.

## Legacy Fence Rationale

The 39 migration files under `supabase/website/_v2/*.sql` were applied to production
**before** the Squawk lint ratchet was introduced. Their byte-level contents are
checksummed in `core._migrations_applied`, and `apply_migrations.py
--check-manifest-fresh` (a CLAUDE.md protected knob) refuses any prod deploy where
the manifest no longer matches the live DB.

Editing those files — even to add inline `-- squawk-ignore` comments — would
silently break the drift gate on the next deploy. Therefore the legacy corpus is
fenced via `excluded_paths` in `.squawk.toml`:

```toml
excluded_paths = ["supabase/website/_v2/*.sql"]
```

This is Squawk's native, first-class exclusion mechanism (since v0.29.0,
2024-05-30). Any new migration outside that glob is linted strictly — the CI
`lint-v2-sql` job no longer carries `continue-on-error: true`.

## Canonical Migration Wrapper

All NEW migrations (placed under a fresh path — see "Future `_v3/`" below — not
`_v2/`) MUST wrap DDL in a transaction with bounded lock/statement timeouts. This
is the Stripe / GitLab consensus pattern for online schema changes against a live
Postgres:

```sql
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '15s';

-- DDL here. Use CREATE INDEX CONCURRENTLY OUTSIDE this transaction; everything
-- else (ALTER TABLE ... ADD COLUMN with default expression of constant, partition
-- attach/detach with the right locks, etc.) goes inside.

COMMIT;
```

Rationale:

- **`lock_timeout = '3s'`** — caps how long the migration will wait for an
  `ACCESS EXCLUSIVE` lock before bailing. Long waits silently queue all reads
  and writes behind the migration; 3s is Stripe's documented ceiling for online
  DDL on a hot table
  ([Stripe — Online migrations at scale](https://stripe.com/blog/online-migrations)).
- **`statement_timeout = '15s'`** — caps how long the DDL itself can run once
  it holds the lock. GitLab uses `15s` as the default lock retry budget for
  zero-downtime DDL
  ([GitLab — Migration Style Guide](https://docs.gitlab.com/ee/development/migration_style_guide.html#retry-mechanism-when-acquiring-database-locks)).
- **`SET LOCAL`** — confines the override to this transaction; no global config
  drift.
- **`BEGIN; ... COMMIT;`** — explicit transaction, so a failure aborts cleanly
  with no partial state. `apply_migrations.py` wraps each file in a transaction
  at runtime as well; the explicit `BEGIN/COMMIT` is for clarity and for cases
  where the file is replayed by hand.

Squawk's `prefer-robust-stmts` rule will warn if the wrapper is missing.

## Future `_v3/` Plan (Deferred)

The first NEW migration after H-2 should NOT be added to `supabase/website/_v2/`.
Instead, create `supabase/website/_v3/` and re-anchor the runtime applier + CI
glob there. Rationale: directory partitioning is the cheapest enforceable
boundary for "pre-ratchet" vs "linted" migrations. Mixing new files into `_v2/`
forces the `excluded_paths` glob to become a hand-maintained allow-list of 39
filenames — defeating the audit trail the ratchet exists to provide.

Concrete steps when the first `_v3/` migration is needed:

1. Create `supabase/website/_v3/` directory.
2. Update `apply_migrations.py` to read both `_v2/` (read-only / frozen) and
   `_v3/` (active) — or migrate the manifest pointer entirely to `_v3/` if `_v2/`
   is finalized.
3. Tighten `.squawk.toml` `excluded_paths` to keep the `_v2/` glob (legacy
   stays fenced forever).
4. The Squawk Action `pattern:` in `migration-ci.yml` already covers
   `supabase/website/_v2/*.sql`; widen to `supabase/website/_v{2,3}/*.sql` or
   point at `_v3/` exclusively at that time.

Deferring the directory split until there is actually a new migration to write
keeps the diff scoped to H-2 and avoids touching the runtime applier without a
forcing function.
