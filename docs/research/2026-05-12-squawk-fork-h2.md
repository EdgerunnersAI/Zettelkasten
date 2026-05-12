# H-2 Fork Decision: Squawk Ratchet Against 39 Frozen Legacy Migrations

**Date:** 2026-05-12
**Status:** Research-only (no code edits)
**Scope:** WAVE-D post-hardening sprint — make Squawk lint gate AUTHORITATIVE for new migrations without touching the 39 already-applied `supabase/website/_v2/*.sql` files (checksums are tracked in `core._migrations_applied`; the drift gate at `apply_migrations.py --check-manifest-fresh` trips on any byte change).

---

## 1. Executive Recommendation

**Adopt Option B** — config-only `excluded_paths` glob in `.squawk.toml` covering the 39 legacy files, plus drop `continue-on-error: true` from the Squawk CI step so the gate becomes authoritative for any **new** migration outside the glob.

Rationale:
- **Zero drift risk.** Option B does not modify a single byte of any tracked migration file; the manifest stays fresh, and the `--check-manifest-fresh` gate (a CLAUDE.md protected knob) is never touched.
- **No prod-DB credential exposure.** Option A requires 39 `--reconcile-checksum` runs against prod, each a protected-knob operator-approval event under the existing manifest-drift doctrine. Option B needs none.
- **Squawk natively supports it.** `excluded_paths` with glob syntax has been a first-class config field since **v0.29.0 (2024-05-30)** and is documented as the canonical exclusion mechanism in the current CLI docs [(Squawk CLI docs)](https://squawkhq.com/docs/cli/). The current release line is `v2.51.0` (2026-05-07) [(Squawk repo)](https://github.com/sbdchd/squawk).
- **Industry consensus 2024–2026** for "ignore legacy, enforce on new" leans toward **config-based path globs** when the linter supports them natively (Ruff `[lint.per-file-ignores]`, ESLint v9 flat-config `ignores`, Biome `files.includes` with negation, Sonar `sonar.exclusions`). Inline `-- squawk-ignore-file` comments (Squawk v2.0.0+) are explicitly equivalent — but require editing the legacy files, which is what we cannot do.

Paste-ready `.squawk.toml` (project root):

```toml
# Postgres version aligned with Supabase managed PG
pg_version = "15.0"
assume_in_transaction = true

# Legacy migrations applied before Squawk ratchet (2026-05-12).
# Cannot be edited: contents are SHA256-checksummed in core._migrations_applied
# and the apply_migrations.py --check-manifest-fresh drift gate would refuse the
# next prod deploy. New migrations added after master SHA 5ce329b must NOT match
# these globs — they are linted strictly.
excluded_paths = [
    "supabase/website/_v2/*.sql",
]

# Opt-in rules we want enforced project-wide
included_rules = [
    "require-concurrent-index-creation",
]

[upload_to_github]
fail_on_violations = true
```

CI step change (conceptual — apply during implementation, not now):

```yaml
- name: Squawk migration lint
  run: squawk **/supabase/**/_v3/*.sql   # NEW migrations live under a fresh path
  # remove: continue-on-error: true
```

If the team prefers to keep new migrations under `_v2/` rather than starting a `_v3/` directory, narrow the glob to the exact 39 filenames (a one-time enumeration) so any **new** file in `_v2/` is linted. Either pattern works under Squawk's glob engine.

---

## 2. Squawk Schema Verification (the load-bearing fact)

Verbatim from the Squawk CLI docs [(Squawk CLI docs)](https://squawkhq.com/docs/cli/):

```toml
pg_version = "11.0"
excluded_rules = [
    "require-concurrent-index-creation",
    "require-concurrent-index-deletion",
]
included_rules = [
    "require-table-schema",
]
assume_in_transaction = true
excluded_paths = [
    "005_user_ids.sql",
    "*user_ids.sql",
]
[upload_to_github]
fail_on_violations = true
```

The `--exclude-path` CLI flag is the imperative twin:

> `--exclude-path <EXCLUDED_PATH>`  Paths to exclude
> `--exclude-path=005_user_ids.sql --exclude-path=009_account_emails.sql`
> `--exclude-path='*user_ids.sql'`

Confirmed in the Squawk CHANGELOG [(Squawk CHANGELOG)](https://github.com/sbdchd/squawk/blob/master/CHANGELOG.md):

- **v0.29.0 (2024-05-30):** added `--excluded-paths` flag and `excluded_paths` config option.
- **v2.0.0 (2025-05-07):** introduced inline ignore comments (`-- squawk-ignore`).
- **v2.19.0 (2025-07-09):** introduced file-level `-- squawk-ignore-file [rule-name]` markers.
- **v2.23.0 (2025-08-20):** LSP quick-fixes for line/file ignores.
- **v2.51.0 (2026-05-07):** latest release at time of writing.

**No baseline / since-commit / scope feature** exists in any released Squawk version through 2026-05-12. If we ever want "lint only files changed since SHA X", we must implement it shell-side — see the Pragmatic Minor section below.

**CLI override semantics:** `--exclude`, `--include`, `--exclude-path`, `--pg-version` always override the config file [(Squawk CLI docs)](https://squawkhq.com/docs/cli/). Keep CI invocations free of these flags so the config is the single source of truth.

---

## 3. Comparison Matrix

| Dimension          | A — Reconcile-checksum + per-file comments      | B — `excluded_paths` config glob (recommended) | C — Ship config, keep `continue-on-error`        |
|--------------------|-------------------------------------------------|------------------------------------------------|--------------------------------------------------|
| Drift risk         | High — 39 file-byte changes + 39 manifest re-writes; trip risk on every prod deploy until all reconciled | None — zero bytes change in any tracked migration | None — zero bytes change                       |
| Gate authority     | Authoritative for new + retro-ignored legacy    | Authoritative for new; legacy invisible to gate | Non-authoritative — defeats the stated goal      |
| Audit trail        | Strong per-file (comment names the rule)        | Single config glob; rationale in `.squawk.toml` comment + git blame | N/A                                            |
| Effort             | High — 39× `--reconcile-checksum` runs against prod + 39 file edits + 39 operator approvals | Low — ~10-line config addition + 1 CI yaml line | Lowest — config only, but useless                |
| Industry precedent | Low — inline comments are the per-issue idiom, not a bulk ratchet tool [(Ruff issue #2446)](https://github.com/astral-sh/ruff/issues/2446) | High — Ruff `[per-file-ignores]`, ESLint `ignores`, Biome `!`-negation, Sonar `sonar.exclusions` all do this [(Ruff settings)](https://docs.astral.sh/ruff/settings/), [(ESLint flat config)](https://eslint.org/docs/latest/use/configure/configuration-files), [(Biome)](https://biomejs.dev/guides/configure-biome/) | N/A                                            |
| Reversibility      | Hard — re-editing 39 files + re-reconciling     | Trivial — delete one toml stanza               | Trivial — flip one yaml line                     |

---

## 4. Industry Consensus 2024–2026

Across the modern linter ecosystem, **config-based path globs are the preferred ratchet mechanism when (a) the linter supports them natively and (b) the legacy files are immutable or expensive to touch.** Inline comments win when the violation is local, the file is being edited anyway, or the developer wants per-line provenance. Ruff documents both `[lint.per-file-ignores]` (globs) and `# noqa` comments as equally first-class and notes that the comment form is for "individual line or file-level exemptions where you want to maintain inline documentation" while glob ignores are "better for systematically handling legacy code in specific directories" [(Ruff config)](https://docs.astral.sh/ruff/configuration/). ESLint v9's flat-config `globalIgnores()` helper was explicitly added in 2025 to make path-based ignores the unambiguous default for "skip whole directories" [(ESLint flat-config-extends 2025)](https://eslint.org/blog/2025/03/flat-config-extends-define-config-global-ignores/). SonarQube goes further with a dedicated "new code" model where the entire legacy corpus is permanently excluded from gate decisions [(Sonar New Code)](https://docs.sonarsource.com/sonarqube-server/user-guide/about-new-code). For our specific constraint — 39 already-checksummed files that we cannot edit — the inline-comment path is technically unavailable, which collapses the choice onto path-glob exclusion. This is the same trade-off `eslint-baseline` and `eslint-formatter-ratchet` codify for JS [(eslint-baseline)](https://github.com/lukahartwig/eslint-baseline), [(eslint-formatter-ratchet)](https://github.com/Jmsa/eslint-formatter-ratchet).

On the migration-tooling side, neither Atlas, Flyway, nor Liquibase has a first-class "lint baseline" concept as of mid-2026. Atlas offers `--git-base "<branch>"` to lint only the diff between branches [(Atlas lint docs)](https://atlasgo.io/versioned/lint), which is the closest analog to a since-commit filter; Squawk has no equivalent. Flyway's `baseline` command [(Flyway baseline)](https://www.red-gate.com/hub/product-learning/flyway/flyways-baseline-migrations-explained-simply) is about migration history (start applying from version X), not lint ratcheting. So the path-glob mechanism is the only portable answer for Squawk users today.

---

## 5. Pragmatic Minor for Our Drift-Gate Setup

Two wrinkles specific to this codebase that the generic pattern doesn't cover:

**(a) Anchor the glob to the SHA-frozen path, not the timestamp.** Future migrations should live under a new directory (e.g., `supabase/website/_v3/` or `supabase/website/migrations/`) rather than continuing to add files into `_v2/`. This makes the `excluded_paths = ["supabase/website/_v2/*.sql"]` glob a permanent, self-documenting fence: every file under `_v2/` is by definition pre-ratchet; every file outside is linted. Mixing new and frozen migrations in the same directory forces the glob to be a hand-maintained allow-list and re-creates the audit problem the ratchet exists to solve. **This is a decision because** directory partitioning is the cheapest enforceable boundary for "legacy vs ratcheted" given that Squawk has no since-commit feature.

**(b) Since-commit emulation if directory split is rejected.** If the team objects to a `_v3/` directory, the fallback is a CI shell filter rather than a Squawk feature. Concretely:

```bash
git diff --name-only --diff-filter=A origin/master...HEAD -- 'supabase/website/_v2/*.sql' \
  | xargs -r squawk
```

This lints only files **added** since master in the current PR — Atlas's `--git-base "master"` pattern, ported to shell because Squawk doesn't ship the flag. Trade-off: the gate stops catching post-merge regressions on master itself (`git diff` against itself is empty). Mitigation: a second CI job that runs `squawk supabase/website/_v2/*.sql` with the `excluded_paths` config on master only — belt-and-braces.

**(c) Reconcile-checksum doctrine.** Even if we ever revisit Option A, the broader industry view is that migration-history checksums are **sacred unless the change is content-equivalent** (whitespace, comments). Flyway documents `repair` for the same purpose and frames it as a "use sparingly" tool [(Flyway repair)](https://www.baeldung.com/spring-boot-flyway-repair). Our existing `--check-manifest-fresh` gate matches this doctrine: it exists precisely so a stray `sed` or auto-formatter cannot silently rewrite a migration's bytes between dev and prod. Bypassing it 39 times to land a lint rule would be the largest single erosion of that invariant since the gate was introduced — and the lint rule does not require it. **This is a decision because** Option B achieves the stated goal with zero erosion; Option A pays a permanent integrity cost for a transitional benefit.

**(d) `require-timeout-settings` is opt-in, not default-on.** Worth confirming during implementation: the 237 warnings imply the rule is being run via `--include require-timeout-settings` or `included_rules`. If we leave it in `included_rules` and add the `_v2/` glob to `excluded_paths`, the rule effectively becomes "enforced on new migrations only" — exactly the H-2 goal — without further plumbing.

---

## 6. Citations

- [Squawk CLI docs — configuration and `excluded_paths`](https://squawkhq.com/docs/cli/)
- [Squawk Quick Start](https://squawkhq.com/docs/)
- [Squawk repository](https://github.com/sbdchd/squawk)
- [Squawk CHANGELOG](https://github.com/sbdchd/squawk/blob/master/CHANGELOG.md)
- [Atlas — Verifying Migration Safety (`migrate lint`, `--git-base`)](https://atlasgo.io/versioned/lint)
- [Atlas Migration Analyzers](https://atlasgo.io/lint/analyzers)
- [Flyway — Baseline Migrations Explained Simply (Redgate)](https://www.red-gate.com/hub/product-learning/flyway/flyways-baseline-migrations-explained-simply)
- [Flyway — Database Drift and How it Happens (Redgate)](https://www.red-gate.com/hub/product-learning/flyway/flyway-database-drift-and-how-it-happens)
- [Flyway Repair with Spring Boot (Baeldung 2024)](https://www.baeldung.com/spring-boot-flyway-repair)
- [Ruff — Configuration (`[lint.per-file-ignores]`)](https://docs.astral.sh/ruff/configuration/)
- [Ruff — Settings](https://docs.astral.sh/ruff/settings/)
- [Ruff issue #2446 — per-file rule disable via comment](https://github.com/astral-sh/ruff/issues/2446)
- [ESLint — Configuration Files (flat-config `ignores`)](https://eslint.org/docs/latest/use/configure/configuration-files)
- [ESLint blog — Evolving flat config with extends, define-config, global-ignores (2025-03)](https://eslint.org/blog/2025/03/flat-config-extends-define-config-global-ignores/)
- [ESLint — Migrate to v9.x](https://eslint.org/docs/latest/use/migrate-to-9.0.0)
- [Biome — Configure Biome (`files.includes` + `!`-negation)](https://biomejs.dev/guides/configure-biome/)
- [SonarQube — Quality standards and new code](https://docs.sonarsource.com/sonarqube-server/user-guide/about-new-code)
- [SonarQube — Narrowing the focus (exclusions)](https://docs.sonarsource.com/sonarqube-server/8.9/project-administration/narrowing-the-focus)
- [eslint-baseline (lukahartwig)](https://github.com/lukahartwig/eslint-baseline)
- [eslint-formatter-ratchet (Jmsa)](https://github.com/Jmsa/eslint-formatter-ratchet)
- [GoCardless — Friday's outage post mortem](https://gocardless.com/blog/fridays-outage-post-mortem/) (cited for migration-discipline framing; not a checksum-reconcile post-mortem — none of the canonical ones surfaced for this query)
