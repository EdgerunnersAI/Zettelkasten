# CI Postgres Image — pg_partman + pg_cron Plan

**Status:** scoped, not yet implemented. Documenting the path forward for a follow-up iteration. Authored 2026-05-11.

## Why this exists

`migrate-v2-fresh` CI job currently fails because `supabase/postgres:17.x` Alpine image does not pre-install `pg_partman`. Production Supabase has pg_partman 5.3.1 (verified live 2026-05-11). The Phase 8.5.R1 defensive variant (commits ccccc62 + 50877e6) wraps the install in apt-get/apk fallbacks that gracefully fail on Alpine, and the job has `continue-on-error: true` so the workflow stays green. But the gate is functionally informational, not authoritative.

## Why not the current defensive variant indefinitely

Per R4 research consensus (Atlas + Flyway + Liquibase + golang-migrate, 2024-2026): a fresh-stack apply gate that can't actually pass is a gate that nobody trusts. Eventually a real schema regression slips through because everyone learned to ignore the red.

## The right shape (industry-standard 2024-2026)

Custom Postgres docker image extending `supabase/postgres:17.x` with pg_partman + pg_cron pre-installed. Industry pattern per Atlas docs ([Managing PostgreSQL Extensions in a Dedicated Migration Process](https://atlasgo.io/faq/manage-extension-only)) and Flyway ([Managing PostgreSQL Extensions Using Flyway](https://www.red-gate.com/hub/product-learning/flyway/managing-postgresql-extensions-using-flyway)) — extensions are platform concern, install at image layer.

## Implementation steps

### Step 1 — Verify supabase/postgres base
- Pull `supabase/postgres:17.6.1.001` locally
- Determine package manager (apt-get vs apk)
- Determine whether `postgresql-17-partman` exists in PGDG repo (Debian) or pg_partman is alpine-packaged

Hypothesis based on 2026-05-11 CI run: Alpine (apt-get not found). Need to confirm whether musl-dev + postgresql17-dev are available for source build.

### Step 2 — Author Dockerfile

`ci/Dockerfile.zettelkasten-postgres`:

```dockerfile
ARG SUPABASE_POSTGRES_TAG=17.6.1.001
FROM supabase/postgres:${SUPABASE_POSTGRES_TAG}
USER root
RUN if command -v apt-get >/dev/null 2>&1; then \
      apt-get update && apt-get install -y --no-install-recommends postgresql-17-partman; \
    elif command -v apk >/dev/null 2>&1; then \
      apk add --no-cache git make gcc musl-dev postgresql17-dev && \
      git clone --depth 1 https://github.com/pgpartman/pg_partman /tmp/pg_partman && \
      cd /tmp/pg_partman && make NO_BGW=1 install && \
      rm -rf /tmp/pg_partman && \
      apk del git make gcc musl-dev postgresql17-dev; \
    fi
USER postgres
```

### Step 3 — Add `supabase/config.toml`

Currently absent. Create with:

```toml
project_id = "zettelkasten-ci"

[db]
image = "ghcr.io/chintanmehta21/zettelkasten-postgres-ci:latest"
# OR for local-build approach (no GHCR push):
# image = "zettelkasten/postgres-with-partman:local"
```

**Decision point:** GHCR-published (multi-developer reuse, persistent) vs locally-built-per-run (no GHCR auth needed, slower CI). For 1-2-dev team, local-build is the lowest-friction first cut.

### Step 4 — Update `.github/workflows/migration-ci.yml`

Insert a new step BEFORE `Boot local Supabase stack`:

```yaml
- name: Build CI Postgres image
  run: |
    docker build \
      -f ci/Dockerfile.zettelkasten-postgres \
      -t zettelkasten/postgres-with-partman:local \
      ci/
```

Adjust `supabase/config.toml` `db.image` to match the local tag. Then `supabase start` should use the custom image instead of the default.

### Step 5 — Remove the runtime apt-get/apk install step

Once Step 4 lands and migrate-v2-fresh observes green, remove the now-redundant defensive install step from migration-ci.yml. Also remove the job-level `continue-on-error: true` hedge.

### Step 6 — Optional: GHCR publish

If multi-developer reuse or pre-warmed image caching becomes valuable, add a separate workflow `build-ci-postgres.yml` that builds + pushes to ghcr.io on push to master that touches `ci/Dockerfile.zettelkasten-postgres`. Tag with image-content hash so PR builds reuse.

## Risks / gotchas

1. **Supabase CLI version compatibility:** the `db.image` config field requires supabase CLI ≥ 1.x — check installed version pinning in workflow.
2. **PGDG repo for postgresql-17-partman:** verify the package name exists in PGDG apt for Debian 12/13 with Postgres 17. As of 2024 it was `postgresql-17-partman`, but check upstream.
3. **Alpine source-build:** musl-dev + postgresql17-dev availability in Alpine 3.18+ repos varies. If unavailable, the Dockerfile's apk branch will fail at image-build time — but that's surfaced loudly at build-time rather than silently at apply-time, which is the correct halt point per R4 industry consensus.
4. **pg_cron is also referenced in 00_extensions.sql** but the current run got past `CREATE EXTENSION pg_cron` (failed only on pg_partman), suggesting pg_cron IS pre-installed in supabase/postgres. Confirm before assuming both need image-layer installs.

## Acceptance criteria

- `gh workflow run migration-ci.yml --ref master` → both jobs PASS (conclusion=success)
- `00_extensions.sql` applies cleanly against the custom image's fresh stack
- `apply_migrations.py --v2 --check-manifest-fresh` exits 0 after the fresh apply
- Job-level `continue-on-error: true` REMOVED — gate is authoritative again
- No regression: prod deploys still green (production Supabase image unaffected; this is CI-only)

## Mem-vault thread

- Initial CI-gate discovery + research: observation `E6JaMsOfar7Rvees6KVNhddV` (2026-05-11)
- Phase 8.5.R1 plan: `docs/superpowers/plans/2026-05-10-phase-8.5-hardening-additions.md`
- Industry pattern citations: Atlas FAQ on extension management 2024-2025, Flyway extensions guide 2024, Supabase Discussion #37986 (Oct 2025), Supabase Issue #1586

---

## Post-WAVE-D update (2026-05-12)

Path A landed. `ci/Dockerfile.zettelkasten-postgres` extends
`supabase/postgres:17.6.1.001` and installs `postgresql-17-partman` via PGDG
apt-get (Debian branch is the working path; Alpine branch retained as fallback).
The image is built and tagged in CI as `supabase/postgres:17.6.1.001` so
`supabase start` resolves the local image automatically — this sidesteps the
still-open `supabase/cli#3688` `[db].image` config-field request.

`00_extensions.sql` now creates `partman` schema BEFORE `CREATE EXTENSION
pg_partman SCHEMA partman` (pg_partman upstream issue #842 — non-relocatable
since 5.4.1).

**`partman.create_parent` deliberately NOT called.** Defer to a later iteration
once any v2 table crosses the 10M-row threshold or shows planning-time
regression in `pg_stat_statements`. Citation: [pganalyze — Partitioning in
Postgres and the risk of high partition counts (2023)](https://pganalyze.com/blog/5mins-postgres-partitioning).
Until then the extension is idle metadata — zero runtime cost.

Runtime apt-get/apk install step REMOVED from the workflow. Job-level
`continue-on-error: true` REMOVED. The Fresh-Supabase gate is authoritative
again per Atlas/Flyway 2024-2026 consensus.
