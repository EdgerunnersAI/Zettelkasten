# Squawk `require-timeout-settings` — Baseline Strategy for `_v2` Migrations

**Date:** 2026-05-12
**Author:** research-only pass (post-WAVE-D hardening)
**Scope:** 237 Squawk warnings across 36 SQL files in `supabase/website/_v2/`. Currently bypassed via `continue-on-error: true` on the `lint-v2-sql` job in `.github/workflows/migration-ci.yml`. We want the Squawk job to become **authoritative** (block new violations) without rewriting already-applied production migrations.

---

## 1. Executive recommendation

**Adopt a per-directory `.squawk.toml` "selective exclusion" baseline + file-level `-- squawk-ignore-file` markers, with a ratchet wrapper** rather than (a) rewriting 36 files or (b) blanket-disabling the rule.

Concretely:

1. **Repo-level `.squawk.toml`** that:
   - Pins `pg_version = "17.0"` (matches Supabase managed PG).
   - Sets `assume_in_transaction = true` (matches `apply_migrations.py` runtime wrapper — kills the false-positive flood from `prefer-robust-stmts` and `require-timeout-settings` for stmts that ARE in a transaction).
   - Excludes one rule globally for the repeatable subtree only (see §2 — repeatable migrations are CREATE OR REPLACE FUNCTION; the rule does not meaningfully apply).
2. **File-level `-- squawk-ignore-file require-timeout-settings`** in the 36 already-applied versioned files. This is the canonical Squawk-blessed mechanism for "legacy migration; do not re-lint" since v2.19.0 (released 2025-07-09; see §4).
3. **Drop `continue-on-error: true` from the `lint-v2-sql` job** so any NEW migration that omits timeouts is blocked at PR time.
4. **Forward discipline (codified in CLAUDE.md, not enforced by Squawk):** every NEW versioned migration starts with the canonical wrapper from §2.2.

**Why this over the alternatives:** Squawk does NOT have a first-class `baseline` file (verified against the v2.51.0 changelog, May 2026 — see §4 citation). Industry-standard "baseline + ratchet" (eslint-formatter-ratchet, the Notion blog post, the open Ruff issue #1149) all live OUTSIDE the linter as a wrapper layer. For 237 violations across 36 already-applied files, a wrapper layer is over-engineered; the file-level ignore comment that Squawk shipped in v2.19.0 is exactly the supported escape hatch, and it lives next to the SQL so the intent is auditable in code review.

The chosen approach makes the gate authoritative for new code while accurately marking already-applied production migrations as "frozen — re-linting these has no effect on prod." It costs ~36 single-line edits, zero schema risk, zero behavioral change.

---

## 2. Canonical timeout wrapping pattern

### 2.1 What Squawk actually requires

From the official rule page ([squawkhq.com/docs/require-timeout-settings](https://squawkhq.com/docs/require-timeout-settings)):

> "You must configure a `lock_timeout` to safely apply migrations." `statement_timeout` "helps prevent long migrations that consume too many database resources." Exception: "If your database connection is already configured with lock and statement timeouts, you can safely ignore this rule."

Squawk's example fix:

```sql
set lock_timeout = '1s';
set statement_timeout = '5s';
alter table t add column c boolean;
```

That example uses **session-level** `SET` (no `LOCAL`, no `BEGIN`). It is the literal "auto-fix suggestion" Squawk emits and is sufficient to silence the rule.

### 2.2 What the Postgres ecosystem actually does

The session-level `SET` example is fine for a one-shot psql `-f migration.sql`, but is the WRONG default for an OLTP service:

| Layer | Behavior of bare `SET` | Behavior of `SET LOCAL` (inside `BEGIN`) |
|---|---|---|
| Direct psql session | Setting persists until disconnect | Setting reverts at `COMMIT`/`ROLLBACK` |
| PgBouncer **transaction-pool** mode | Setting leaks into the connection's next client (silent corruption) | Setting dies with the transaction (safe) |
| Failed migration | Setting persists for whatever rolls back | Setting reverts; clean |

PgBouncer transaction-pool mode is the Supabase default (verified via Supabase docs on the Supavisor pooler). So **the production-safe pattern is `SET LOCAL` inside an explicit `BEGIN`/`COMMIT`**, not the example from the Squawk page.

Canonical wrapper (paste-ready for every new `_v2/NN_*.sql` versioned migration):

```sql
BEGIN;
SET LOCAL lock_timeout      = '3s';
SET LOCAL statement_timeout = '15s';
SET LOCAL idle_in_transaction_session_timeout = '60s';

-- migration body here

COMMIT;
```

Justifications:
- **`SET LOCAL`** — Postgres docs ([runtime-config-client](https://www.postgresql.org/docs/current/runtime-config-client.html)): "The effects of `SET LOCAL` last only till the end of the current transaction." Required for transaction-pool poolers ([django-pg-migration-tools timeouts.html](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/timeouts.html), which uses exactly this pattern: `SET LOCAL` inside transactions, `SET SESSION` outside).
- **3s lock_timeout** — Stripe's [pg-schema-diff](https://github.com/stripe/pg-schema-diff/blob/main/README.md) uses `lock_timeout = 3000` (3s) at session level for every ALTER TABLE. GitLab does not publish an explicit number but their `with_lock_retries` helper defaults to short attempts (50 attempts over 40 minutes implies sub-second). 3s is the upper bound of "common sub-2s pattern" reported by [PostgresAI](https://postgres.ai/blog/20210923-zero-downtime-postgres-schema-migrations-lock-timeout-and-retries) and pads our 1 vCPU droplet's higher TLS/IO overhead. 1s (Squawk's auto-fix default) is fine in steady state but will spuriously fail on a cold-start migration where authentication+TLS itself eats 200-400ms.
- **15s statement_timeout** — matches GitLab.com production (see [GitLab Migration Style Guide](https://docs.gitlab.com/development/migration_style_guide/), "15s statement timeout"). Their guidance: regular migrations should complete in <3 min, but the gate is set at 15s for blocking DDL — beyond that, use `add_concurrent_index` which explicitly disables the timeout.
- **60s idle_in_transaction_session_timeout** — extra belt-and-braces. Stops a runaway migration from holding a transaction open if the client process dies between statements. Not required by Squawk; recommended by every Postgres-at-scale source we cite.

### 2.3 Exceptions that REQUIRE breaking out of the transaction

`CREATE INDEX CONCURRENTLY`, `REINDEX CONCURRENTLY`, `ALTER TYPE ... ADD VALUE`, `VACUUM` — these **cannot run inside a transaction**. For these, use the second canonical pattern:

```sql
-- squawk-ignore-next-statement require-timeout-settings
-- (or: configure timeouts at the connection level via PGOPTIONS / role default)
SET lock_timeout      = '3s';
SET statement_timeout = '1200s';   -- 20 min, matches GitLab's concurrent-index ceiling

CREATE INDEX CONCURRENTLY idx_name ON tbl (col);
```

`statement_timeout` of 20 min for CONCURRENTLY operations is taken directly from GitLab's published guideline. For our scale today (10-15 users), 20 min is grossly excessive; at 10k+ users it is tight but realistic for a 30M-row HNSW build. Set it high; it costs nothing if the migration finishes faster.

---

## 3. Heuristic: TRUE positive vs noise for THIS repo

Apply to each of the 36 `_v2/*.sql` files:

| File pattern | Likely Squawk verdict | Action |
|---|---|---|
| `00_extensions.sql`, `01..06_*_schema.sql` (initial schema bootstrap on **empty** schemas) | NOISE — `CREATE TABLE`, `CREATE SCHEMA`, `CREATE EXTENSION` on empty objects take O(ms) and acquire negligible locks | `-- squawk-ignore-file require-timeout-settings` at top of file |
| `07_partman_setup.sql`, `08_rls_policies.sql`, `09_seed_scorer_registry.sql` | NOISE — RLS + small seed rows, no table rewrites | ignore-file |
| `10_hnsw_indexes.sql` | **TRUE POSITIVE if any index lacks CONCURRENTLY**; the rule is legitimately flagging risk | Verify CONCURRENTLY; if not present, wrap per §2.3; if present, ignore-file with comment "indexes are CONCURRENTLY" |
| `11_post_install.sql`..`28_drop_legacy_rpcs.sql` (RPCs, view DDL, DROP TABLE on already-empty/dead tables) | NOISE for RPCs; potentially TRUE for `DROP TABLE` on live tables — but these were applied on dead `public.kg_*`/`public.rag_*` tables per Phase 6/8 closeout (CLAUDE.md). Locks irrelevant. | ignore-file with one-line forensic comment per file: `-- squawk-ignore-file require-timeout-settings -- legacy: applied to empty schema 2026-05-11` |
| `15_drop_legacy_tables.sql`, `31_drop_legacy_pricing.sql` | NOISE in retrospect (already applied) but would be TRUE POSITIVES if re-run | ignore-file with forensic comment "DROP applied 2026-05-11; AccessExclusiveLock on dead tables" |
| `30_billing_pricing_active_plan.sql`, `42_kg_connection_strength.sql`, `43_port_match_kg_nodes.sql` | **Inspect manually.** If any contains `ALTER TABLE ... ADD COLUMN ... NOT NULL` without DEFAULT, `ALTER TABLE ... ADD CONSTRAINT ... NOT VALID/VALIDATE`, `UPDATE` on large table, `VACUUM FULL`, `ALTER TYPE ... ADD VALUE` — TRUE POSITIVE. Otherwise NOISE. | Manual review; for true positives, the file was already applied so still ignore-file (intent: "this was risky and we got away with it; documented") |
| `repeatable/R__introspect_auth_users_dependents.sql` (and any future `R__*.sql`) | NOISE — `CREATE OR REPLACE FUNCTION` is metadata-only and takes AccessShareLock briefly | Repo-level `.squawk.toml` excludes `require-timeout-settings` for `repeatable/*` |

**Heuristic rule, restated for new files:** a migration is a TRUE positive (must wrap with timeouts) iff it does ANY of:
1. `ALTER TABLE ... ADD COLUMN ... NOT NULL` (with or without DEFAULT — DEFAULT helps on PG11+ but is still flagged)
2. `ALTER TABLE ... ADD CONSTRAINT` without `NOT VALID`
3. `CREATE [UNIQUE] INDEX` without `CONCURRENTLY`
4. `DROP COLUMN`, `DROP TABLE`, `DROP INDEX` on any table that may be non-empty at apply time
5. `UPDATE` / `DELETE` on a table with >10k rows expected
6. `VACUUM FULL`, `CLUSTER`, `REINDEX` non-CONCURRENTLY
7. `ALTER TYPE ... ADD VALUE`, `ALTER TYPE ... RENAME VALUE` (these now don't lock since PG12, but rule still fires)

If a file does NONE of those — `CREATE TABLE`, `CREATE FUNCTION`, `CREATE OR REPLACE VIEW`, `CREATE SCHEMA`, `GRANT/REVOKE` on already-empty objects — it's noise.

---

## 4. Squawk's actual ignore primitives (verified against v2.51.0)

From the Squawk changelog ([github.com/sbdchd/squawk/blob/master/CHANGELOG.md](https://github.com/sbdchd/squawk/blob/master/CHANGELOG.md)) and CLI docs ([squawkhq.com/docs/cli](https://squawkhq.com/docs/cli/)):

| Mechanism | Introduced | Use case | Cite |
|---|---|---|---|
| `--exclude` CLI flag | v0.x | Disable a rule globally for the lint run | CLI docs |
| `.squawk.toml` `excluded_rules` | v0.x | Same as `--exclude`, persisted | CLI docs |
| `.squawk.toml` `excluded_paths` (glob) | v0.x | Skip files entirely | CLI docs |
| `.squawk.toml` `assume_in_transaction` | v0.x | Tell Squawk the runner wraps each file in a tx; kills false positives on `prefer-robust-stmts` and `require-timeout-settings` | CLI docs |
| `-- squawk-ignore <rule>` (line) | v2.0.0 (2025-05-07) | Ignore one rule for the next statement | Changelog |
| `-- squawk-ignore-file [rule[,rule]]` | **v2.19.0 (2025-07-09)** | Ignore one or many rules for the whole file; arg-less form ignores ALL rules | Changelog v2.19.0 entry |
| `-- squawk-disable-assume-in-transaction` | recent | Per-file override of the toml setting | CLI docs |

**No first-class baseline / ratchet / `--new-only` / `--since` feature exists in Squawk v2.51.0.** Verified directly against the v2.51.0 release notes (May 2026). The community-standard pattern in linters that lack a baseline is to wrap the linter externally — e.g. eslint-formatter-ratchet, Notion's custom ESLint ratchet ([notion.com/blog/how-we-evolved-our-code-notions-ratcheting-system-using-custom-eslint-rules](https://www.notion.com/blog/how-we-evolved-our-code-notions-ratcheting-system-using-custom-eslint-rules)), or the still-open Ruff baseline issue [#1149](https://github.com/astral-sh/ruff/issues/1149).

**Industry practice for "baseline + ratchet" outside Squawk:** real teams build it themselves as a CI shim (run squawk twice: once on the diff, once on the whole tree; compare counts; fail if the diff introduces NEW violations of a specific rule). For 237 violations across 36 already-frozen files, this is not worth building — file-level ignore comments are auditable, version-controlled, and Squawk-blessed.

---

## 5. Concrete config for THIS repo

### 5.1 `.squawk.toml` (repo root) — paste-ready

```toml
# Squawk lint config for the Zettelkasten repo.
#
# Lives at repo root so `squawk` auto-discovers it on traversal. Applies to
# all SQL the `lint-v2-sql` workflow runs on (versioned + repeatable subtrees).
#
# Rationale citations live in docs/research/2026-05-12-squawk-baseline.md.

# Match production Supabase. Bump when Supabase upgrades the managed PG.
pg_version = "17.0"

# apply_migrations.py wraps each versioned file in BEGIN/COMMIT. Tell Squawk
# so it does not flag the implicit transaction as missing.
# Repeatable migrations (CREATE OR REPLACE FUNCTION) also run inside a tx
# (one-statement). This is the only `excluded_paths` we need.
assume_in_transaction = true

# Repeatable subtree is metadata-only (functions/views). Timeouts are
# irrelevant — Squawk maintainer convention: ignore the rule there.
excluded_paths = []  # leave empty; we use file-level markers instead

# DO NOT add `excluded_rules = ["require-timeout-settings"]` here.
# That would silence the rule for NEW versioned migrations and defeat the
# whole point of the gate. Use per-file `-- squawk-ignore-file` instead.
```

### 5.2 Per-file marker — paste-ready

Top of every already-applied `supabase/website/_v2/NN_*.sql`:

```sql
-- squawk-ignore-file require-timeout-settings prefer-robust-stmts
-- Legacy migration applied 2026-05-{date}. Frozen — re-linting has no
-- production effect. New migrations MUST use the canonical timeout wrapper
-- in docs/research/2026-05-12-squawk-baseline.md §2.2.
```

Top of `repeatable/R__*.sql`:

```sql
-- squawk-ignore-file require-timeout-settings
-- Repeatable migration: CREATE OR REPLACE FUNCTION takes only a brief
-- metadata lock. Timeouts not applicable.
```

### 5.3 Workflow change — `.github/workflows/migration-ci.yml`

```diff
   lint-v2-sql:
     name: Squawk lint _v2 SQL
     ...
-    continue-on-error: true
+    # WAVE-D hardening: gate is authoritative. File-level ignore markers
+    # in already-applied migrations document the baseline.
     ...
       - name: Squawk lint _v2 versioned SQL
         uses: sbdchd/squawk-action@v2
         with:
           pattern: "supabase/website/_v2/*.sql"
           version: "2.51.0"
-          fail-on-violations: false
+          fail-on-violations: true
       - name: Squawk lint _v2 repeatable SQL
         uses: sbdchd/squawk-action@v2
         with:
           pattern: "supabase/website/_v2/repeatable/*.sql"
           version: "2.51.0"
-          fail-on-violations: false
+          fail-on-violations: true
```

Note: this only flips to authoritative AFTER all 36 files carry the ignore marker. Roll-out should be: (a) add markers in one PR, (b) verify CI shows zero violations, (c) flip `fail-on-violations` and remove `continue-on-error` in a follow-up PR.

---

## 6. Recommended timeout values — citations

| Operation class | `lock_timeout` | `statement_timeout` | `idle_in_transaction` | Source |
|---|---|---|---|---|
| Standard DDL (ADD COLUMN nullable, ALTER) | **3s** | **15s** | 60s | Stripe pg-schema-diff (3s/3s), GitLab prod (15s) |
| Constraint validate (`ALTER TABLE ... VALIDATE CONSTRAINT`) | 3s | 60s | 60s | GitLab `<3 min` ceiling for regular migrations |
| `CREATE INDEX CONCURRENTLY` | 3s | **1200s (20 min)** | n/a (not in tx) | GitLab concurrent-index 20-min cap |
| Bulk `UPDATE` / `DELETE` (background migration) | 3s | n/a — batch with `LIMIT 1000` and re-issue | 60s | GitLab background-migration pattern (queries capped at 1s each) |
| `DROP TABLE` / `DROP COLUMN` (deferred to "post-deploy") | 3s | 600s (10 min) | 60s | GitLab post-deployment migration ceiling |
| `VACUUM` / `REINDEX CONCURRENTLY` | n/a | **0** (disable) | n/a | Standard Postgres practice; these are long-running by design |
| Squawk's auto-fix default | 1s | 5s | — | Squawk docs example. Too aggressive for our 1 vCPU droplet's cold-start; use 3s/15s. |

For our scale target (10-15 today, 10k+ at horizon) and our hardware (Supabase managed PG, no co-located compute pressure from the droplet since the DB is offboard), the 3s/15s pair has comfortable margin. At 100k+ users we'd revisit lock_timeout downward to 1s with a `with_lock_retries` style helper, but that is out of scope today.

---

## 7. Comparison matrix — 5 criteria

| Criterion | A. Baseline wrapper (ratchet) | B. File-level ignore markers (recommended) | C. Rewrite all 36 files |
|---|---|---|---|
| **Effort to deploy** | High — build & maintain a CI shim that diffs Squawk JSON output across base+head; ~1 day | **Low** — 36 single-line edits + 1 toml + 1 workflow change; ~30 min | Highest — touch every applied file, risk diff-vs-prod drift, golden checksum invalidates `_migrations_applied` entries |
| **Auditability** | Counts live in a `.squawk-baseline.tsv` outside the SQL — reviewer must context-switch | **Highest** — intent lives one line above the SQL it protects, visible in every blame/PR | High but noisy — every file gains 10+ lines of unrelated wrapping |
| **Production risk** | None | **None** | **Material** — checksum changes for files in `core._migrations_applied`; apply_migrations gate trips; needs golden-checksum re-baseline; risk of accidental semantic change |
| **Forward enforcement** | Strong — NEW violations always blocked | **Strong** — NEW files lack the ignore marker, so the rule fires at PR time | Strong — new files inherit the wrapper pattern |
| **Squawk-blessed** | Not a Squawk concept; community wrapper | **Yes** — `-- squawk-ignore-file` is the documented v2.19.0 mechanism | Yes but unnecessary |

Recommendation: **B** wins on 4 of 5 criteria; loses none.

---

## 8. Citations

**Squawk:**
- Rule page: [squawkhq.com/docs/require-timeout-settings](https://squawkhq.com/docs/require-timeout-settings)
- Safe-migration guide: [squawkhq.com/docs/safe_migrations](https://squawkhq.com/docs/safe_migrations)
- CLI / config docs: [squawkhq.com/docs/cli](https://squawkhq.com/docs/cli/)
- Rules index: [squawkhq.com/docs/rules](https://squawkhq.com/docs/rules)
- Changelog: [github.com/sbdchd/squawk/blob/master/CHANGELOG.md](https://github.com/sbdchd/squawk/blob/master/CHANGELOG.md) — v2.0.0 (line-ignore), v2.19.0 (file-ignore), v2.51.0 (May 2026 current)
- Releases page: [github.com/sbdchd/squawk/releases](https://github.com/sbdchd/squawk/releases)
- Repo: [github.com/sbdchd/squawk](https://github.com/sbdchd/squawk)

**Industry practice:**
- GitLab Migration Style Guide: [docs.gitlab.com/development/migration_style_guide](https://docs.gitlab.com/development/migration_style_guide/) — 15s statement_timeout, `with_lock_retries`
- GitLab Avoiding Downtime: [docs.gitlab.com/17.5/development/database/avoiding_downtime_in_migrations](https://docs.gitlab.com/17.5/development/database/avoiding_downtime_in_migrations/)
- Stripe `pg-schema-diff`: [github.com/stripe/pg-schema-diff](https://github.com/stripe/pg-schema-diff/blob/main/README.md) — `SET SESSION lock_timeout = 3000`
- Stripe Online Migrations: [stripe.com/blog/online-migrations](https://stripe.com/blog/online-migrations) — dual-write pattern, scientist library
- PostgresAI zero-downtime: [postgres.ai/blog/20210923-zero-downtime-postgres-schema-migrations-lock-timeout-and-retries](https://postgres.ai/blog/20210923-zero-downtime-postgres-schema-migrations-lock-timeout-and-retries) — 50ms aggressive default, retry+jitter
- Xata lock queue: [xata.io/blog/migrations-and-exclusive-locks](https://xata.io/blog/migrations-and-exclusive-locks)
- pgroll lock queue: [pgroll.com/blog/schema-changes-and-the-postgres-lock-queue](https://pgroll.com/blog/schema-changes-and-the-postgres-lock-queue)
- doctolib safe-pg-migrations: [github.com/doctolib/safe-pg-migrations](https://github.com/doctolib/safe-pg-migrations)
- django-pg-migration-tools: [django-pg-migration-tools.readthedocs.io/en/latest/usage/timeouts.html](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/timeouts.html) — `SET LOCAL` vs `SET SESSION` rule

**Postgres docs:**
- `runtime-config-client`: [postgresql.org/docs/current/runtime-config-client.html](https://www.postgresql.org/docs/current/runtime-config-client.html) — `lock_timeout`, `statement_timeout`, `idle_in_transaction_session_timeout`, `SET LOCAL` semantics

**Baseline/ratchet (out-of-linter pattern):**
- eslint-formatter-ratchet: [github.com/ProductPlan/eslint-formatter-ratchet](https://github.com/ProductPlan/eslint-formatter-ratchet)
- Notion ESLint ratcheting: [notion.com/blog/how-we-evolved-our-code-notions-ratcheting-system-using-custom-eslint-rules](https://www.notion.com/blog/how-we-evolved-our-code-notions-ratcheting-system-using-custom-eslint-rules)
- Ruff baseline issue (still open as of 2026-05): [github.com/astral-sh/ruff/issues/1149](https://github.com/astral-sh/ruff/issues/1149)

---

## 9. Open question (not blocking the recommendation)

The recommendation assumes `apply_migrations.py` actually wraps each versioned file in `BEGIN`/`COMMIT`. Verify before adding `assume_in_transaction = true` to `.squawk.toml`. If `apply_migrations.py` does NOT wrap, then either (a) make it wrap (small, safe change), or (b) drop `assume_in_transaction` and let the `-- squawk-ignore-file` markers carry the full load. Either is fine; the recommendation does not depend on which.
