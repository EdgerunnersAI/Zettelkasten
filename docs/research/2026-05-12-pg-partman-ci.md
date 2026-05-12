# pg_partman CI Provisioning — Research (2026-05-12)

**Scope:** Post-WAVE-D hardening. How major engineering orgs in 2024-2026 install `pg_partman` in CI when the `supabase start` stack's `supabase/postgres:17.x` Alpine image does not pre-ship it.
**Constraint context:** Production = Supabase managed Postgres 17 + pg_partman 5.3.1; app = FastAPI on a 2 GB / 1 vCPU DigitalOcean droplet; data shape is unbounded (any zettel type, any kasten, any user query).
**Author:** research-only pass; no code edits.

---

## 1. Executive recommendation (one sentence)

**Adopt Path A-prime: extend `supabase/postgres:17.x` in a 12-line `ci/Dockerfile` that runs `CREATE SCHEMA partman` ordering correctly, wire it via `supabase/config.toml` `[db].image`, and pin to `ghcr.io/dbsystel/postgresql-partman:17-5` as a fallback reference image** — this is the only path that is (a) authoritative (CI fail = real regression), (b) supabase-start parity-preserving, (c) reproducible without a runtime apk/source-build, and (d) cited by Atlas and Flyway as the industry-standard 2024-2026 pattern for extensions absent from the CI base image. ([dbsystel/postgresql-partman-container](https://github.com/dbsystel/postgresql-partman-container), [Atlas FAQ — Managing Postgres Extensions](https://atlasgo.io/faq/manage-extension-only), [Redgate — Managing Postgres Extensions Using Flyway](https://www.red-gate.com/hub/product-learning/flyway/managing-postgresql-extensions-using-flyway))

---

## 2. Why Path E failed (CREATE EXTENSION schema pre-create issue) and the fix

Path E (apk install inside running container, then `CREATE EXTENSION pg_partman SCHEMA partman`) hit a hard error in pg_partman 5.4.1+: **the extension is no longer relocatable and the target schema must exist BEFORE `CREATE EXTENSION`.** Upstream confirms this as an intentional security fix, not a bug to wait out:

- pg_partman issue #842 ("schema 'partman' does not exist (SQLSTATE 3F000)") is labelled both **confirmed bug** and **fixed in current release** — the "fix" is documentation, not behaviour. v5.4.1 hardened schema qualification; callers must now explicitly create the schema and reference partman objects as `partman.<name>`. ([pgpartman/pg_partman#842](https://github.com/pgpartman/pg_partman/issues/842))
- The README's install recipe is two statements, in this order: `CREATE SCHEMA partman; CREATE EXTENSION pg_partman SCHEMA partman;` — "Schema is optional (but recommended) and can be whatever you wish, but it cannot be changed after installation." ([pgpartman/pg_partman README](https://github.com/pgpartman/pg_partman))
- AWS RDS docs corroborate the same ordering for the managed variant. ([AWS — Managing PG partitions with pg_partman](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL_Partitions.html))

**Fix to apply if we ever retry Path E:** split `00_extensions.sql` so the partman-creating statements run in this order against a v2 baseline that does **not** assume the schema exists:

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE SCHEMA IF NOT EXISTS partman;          -- must precede CREATE EXTENSION
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;
```

This is also the correct shape for Path A — the custom image still requires the v2 migrations to do the schema-first ordering, because the image only pre-installs the *binaries / control files*, not the database-level extension record.

---

## 3. Comparison matrix (A-E × six criteria)

Criteria scored from worst (1) to best (5).

| Path | Description | Viability | Maintenance | CI time | Registry deps | supabase-start parity | Scale-fit |
|---|---|---|---|---|---|---|---|
| **A** | Custom Dockerfile `FROM supabase/postgres:17.x` + source-build inside | 4 | 4 | 3 (one-time build, then cached) | 0 (local build) | 5 | 5 |
| **A-prime** (recommended) | Same as A, but reference `ghcr.io/dbsystel/postgresql-partman:17-5` as fallback for cross-validation | 5 | 5 | 4 (pre-built image pull) | 1 (GHCR pull only, no auth) | 5 | 5 |
| **B** | Abandon `supabase start`; run `postgres:17-bookworm` in compose with PGDG `postgresql-17-partman` | 3 | 2 | 4 | 0 | 1 (loses GoTrue/PostgREST/Storage parity) | 5 |
| **C** | Keep `continue-on-error: true` band-aid | 1 | 5 | 5 | 0 | 5 | 5 |
| **D** | Downgrade Supabase CLI to a Debian-shipping postgres tag | 1 (no such tag exists for PG17) | 1 | 5 | 0 | 5 | 5 |
| **E** | apk source-build at runtime inside live `supabase_db` container | 2 | 2 | 2 | 0 | 5 | 5 |

**Notes:**
- Path C ("informational gate") is explicitly rejected by Atlas and Flyway as anti-pattern. "Database drift is the unintentional divergence of a database schema from its version-controlled state" — a gate that cannot fail does not catch drift. ([PostgreSQL.org — Flyway Community Drift Check](https://www.postgresql.org/about/news/flyway-community-drift-check-released-2970/))
- Path B loses parity with `supabase start`'s GoTrue/PostgREST/Storage sidecars, which our v2 CI uses to validate RLS + RPC. Loss-of-parity cost ≫ extension-install cost. ([Supabase CLI Config](https://supabase.com/docs/guides/cli/config))
- Path D is not viable: there is no Supabase CLI version that ships a Debian-based PG17 image. The PG17 base on Supabase is Alpine only as of 2026-05. ([supabase/postgres on Docker Hub](https://hub.docker.com/r/supabase/postgres))
- Path E's runtime build also drags `git`, `make`, `gcc`, `musl-dev`, `postgresql17-dev` into every CI run (~80-120 MB of installs, ~45-90s of wall time), versus a one-time image-layer cost. Atlas explicitly warns that "extensions are managed at the database level and can only be installed once per database" — running install logic on every CI boot is the wrong layer. ([Atlas FAQ — Managing Postgres Extensions](https://atlasgo.io/faq/manage-extension-only))

---

## 4. Pragmatic minor modifications for OUR case (2 GB / 1 vCPU / Supabase v2 / dynamic data)

### 4.1 Registry choice: pull-not-build, but keep our own Dockerfile as the fallback

- **Primary:** reference `ghcr.io/dbsystel/postgresql-partman:17-5` in `supabase/config.toml` `[db].image` if/when supabase CLI lands the `db.image` field (status: feature request [supabase/cli#3688](https://github.com/supabase/cli/issues/3688) opened 2025-06-09, **not yet merged** as of CLI v2.99.0-beta.6). The dbsystel image: Apache-2.0, nightly-built, PG14-18 × partman 4|5, official postgres base (switched off bitnami August 2025). ([dbsystel/postgresql-partman-container](https://github.com/dbsystel/postgresql-partman-container))
- **Risk:** dbsystel's image is `FROM postgres:17-alpine` (official), not `FROM supabase/postgres:17.x`. That means it ships **plain Postgres + pg_partman + pg_jobmon**, missing every Supabase-specific extension (`vector`, `pg_graphql`, `pgsodium`, `pg_net`, ...) that v2 migrations may touch. Verify v2 baseline against the image first; if a Supabase-only extension is referenced in `_v2/`, fall back to Path A.
- **Fallback (Path A):** our own `ci/Dockerfile.zettelkasten-postgres` extending `supabase/postgres:17.6.x` — gives us BOTH the Supabase extensions AND partman. The 12-line Dockerfile in the existing plan stays, but the `apk` branch needs the schema-creation note from §2 baked into the migration, not the image.

### 4.2 Supabase CLI `db.image` field — current status

The cleanest wiring (a single line in `supabase/config.toml`):

```toml
[db]
image = "ghcr.io/chintanmehta21/zettelkasten-postgres-ci:17.6-partman-5.3"
```

is **not yet supported as of supabase CLI v2.99.0-beta.6 (2026-05).** The feature was requested in [supabase/cli#3688](https://github.com/supabase/cli/issues/3688) (2025-06-09) and remains open. Interim workaround: build the image with the **same tag as the supabase CLI expects** (`supabase/postgres:<exact-tag>`) so `supabase start` picks it up by name, OR use docker-compose override via `supabase/docker/docker-compose.override.yml` to point the `db` service at our local tag. ([Supabase CLI Config](https://supabase.com/docs/guides/cli/config))

### 4.3 Scale-fit: do we actually need pg_partman at 10-15 users today?

**No — but the migrations already reference partman, so the question is "when does the cost pay off," not "should we add it."**

- pg_partman docs: "subpartitioning provides next to NO PERFORMANCE BENEFIT outside of extremely large data in a single partition set (100s of terabytes, petabytes)." For our scale, single-level range-on-time is the only justified shape. ([Crunchy Data — pg_partman docs](https://access.crunchydata.com/documentation/pg-partman/latest/pg_partman/))
- pganalyze's 2023 analysis: 10M rows is the **threshold where architects begin considering** declarative partitioning; below 10M, a single flat table + standard btree is almost always faster, and **excessive partitions add a planning-time penalty** (cited example: 0.235 ms execution + 0.7 ms planning when over-partitioned). ([pganalyze — Partitioning in Postgres and the risk of high partition counts](https://pganalyze.com/blog/5mins-postgres-partitioning))
- ChartMogul case (cited by pganalyze): switching from list partitioning (one-per-customer, hundreds of partitions) to hash partitioning with 30 partitions gave **5× SELECT and 3× JOIN** improvement. The lesson: partition count must stay in the dozens, not hundreds. For multi-tenant data on workspace_id, this means HASH(workspace_id) with a small modulus, NOT one-partition-per-workspace.
- Production case study — Wemolo: 1.5 TB table, **range-on-`created_at` 7-day intervals**, view+trigger redirection, zero downtime. This is the canonical pattern for time-series-shaped workloads (events, logs, ingests). ([Tackling a 1.5 TB Table with pg_partman](https://medium.com/@syedfahadkhalid93/tackling-a-1-5tb-table-near-zero-downtime-partitioning-in-postgresql-using-pg-partman-7e2ae55b9b4f))
- Finextra deep-dive cites financial-services workloads using pg_partman with monthly retention + S3 detach-and-archive — same time-range shape. ([Finextra — pg_partman deep dive](https://www.finextra.com/blogposting/30616/a-technical-deep-dive-into-pgpartman-simplifying-partition-lifecycle-management-at-scale))

**Alternatives that fit our 2 GB / 1 vCPU constraint:**
- **BRIN indexes** on `created_at` for append-only event-shaped tables: ~1000× smaller than btree, near-zero maintenance, ideal for time-correlated inserts. Not in search hits as a direct pg_partman substitute, but the standard "before-partitioning" gradient.
- **Native declarative partitioning without pg_partman:** PG17 supports `PARTITION BY RANGE` directly. pg_partman's *only* added value is automated child-table creation + retention on a schedule. For ≤10 K monthly rows per workspace, a single-flat-table + BRIN beats partitioning until ~10 M rows. ([PostgreSQL 18 docs — Table Partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html))
- **Citus / Timescale:** overkill for 2 GB droplet; Timescale's hypertables are stronger than pg_partman for true time-series but lock us out of vanilla Postgres semantics. ([Tiger Data — pg_partman vs. hypertables](https://www.tigerdata.com/learn/pg_partman-vs-hypertables-for-postgres-partitioning))

**Recommendation:** keep the pg_partman dependency in v2 migrations (cheap to leave in, expensive to add later), but **do not call `create_parent` on any table until that table exceeds 10 M rows or shows planning-time degradation in pg_stat_statements**. Until then, a btree + BRIN on the partition key is sufficient, and partman's BGW maintenance is idle — no cost.

### 4.4 Production case studies on managed pg_partman (cited)

- **AWS RDS** ships pg_partman as a first-class managed extension on Aurora/RDS Postgres. Documented usage pattern: `CREATE SCHEMA partman` → `CREATE EXTENSION pg_partman` → `partman.create_parent(...)`. ([AWS RDS — pg_partman](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL_Partitions.html))
- **Neon** documents pg_partman as a first-class extension (same install order). ([Neon — pg_partman](https://neon.com/docs/extensions/pg_partman))
- **Crunchy Data** maintains the canonical pg_partman fork and IoT case studies (Postgres + Citus + Partman). ([Crunchy Data — Postgres + Citus + Partman IoT](https://www.crunchydata.com/blog/postgres-citus-partman-your-iot-database))
- **Supabase**: NOT pre-installed as of 2026-05; addition "in progress" per maintainer reply 2025-10-28. ([supabase/discussions#37986](https://github.com/orgs/supabase/discussions/37986))
- **No public Stripe / GitLab / Notion case studies** for pg_partman specifically — those orgs publish Vitess (Stripe), partitioned-by-tenant (GitLab uses native PG declarative), Citus-style (Notion) patterns, but pg_partman is largely an SMB / mid-market managed-postgres pattern. Flag explicit: the original ask cited names that aren't represented in the literature; the substantive 2024-2026 case studies are Wemolo (1.5 TB), Crunchy IoT customers, and AWS Aurora reference architectures.

### 4.5 Atlas / Flyway / Liquibase / sqlx-cli — extension drift consensus (2024-2026)

- **Atlas** (v0.20, March 2024, Pro tier): separate extension migrations via `--include "*[type=extension]"`; pre-install in dev/CI/prod via docker `baseline` script; explicitly notes "extensions are managed at the database level and can only be installed once per database." Atlas does NOT solve the "extension missing from CI image" problem — it assumes a `docker` block can install upfront. ([Atlas FAQ — Managing Postgres Extensions](https://atlasgo.io/faq/manage-extension-only), [Atlas v0.20 announcement](https://atlasgo.io/blog/2024/03/18/atlas-v-0-20))
- **Flyway** (Teams/Enterprise tier): tracks extensions + versions automatically, releases Drift Check in Flyway 10.20.1+ (preview, PG12-17). Same assumption: install at image layer. ([Redgate — Managing Postgres Extensions Using Flyway](https://www.red-gate.com/hub/product-learning/flyway/managing-postgresql-extensions-using-flyway), [PostgreSQL.org — Flyway Drift Check](https://www.postgresql.org/about/news/flyway-community-drift-check-released-2970/))
- **Liquibase / sqlx-cli**: no extension-aware drift detection documented; both treat extensions as opaque DDL.
- **2026 consensus:** pre-built CI image with extensions baked in is the dominant pattern across all four tools. The split is GHCR-published (multi-developer reuse) vs locally-built-per-run (single-dev simplicity). For our 1-2 dev team, locally-built first cut is the lowest-friction path; GHCR-publish is the right next step once a second contributor lands. ([dbsystel/postgresql-partman-container](https://github.com/dbsystel/postgresql-partman-container))

---

## 5. Concrete next move

1. **Land Path A** as already scoped in `docs/db-v2/ci-postgres-image-plan.md` Steps 1-5, with the §2 schema-ordering fix applied to `_v2/00_extensions.sql` regardless of which path runs.
2. **Verify v2 baseline against `ghcr.io/dbsystel/postgresql-partman:17-5`** as a one-shot test to confirm whether dbsystel's plain-postgres-alpine image suffices, or whether we genuinely need the supabase-base layer (vector/pg_graphql/pgsodium/pg_net). This is a 10-minute experiment that decides between A-prime (pull dbsystel) and A (build our own).
3. **Defer GHCR publish (Step 6 of existing plan)** until a second contributor joins or until image-build wall-time becomes a CI bottleneck. ≤1-dev team derives no value from pushing a custom image.
4. **Do NOT call `partman.create_parent`** on any v2 table in the current schema migrations. Leave the extension installed and the partman schema present; ship `create_parent` calls in a separate iteration once the relevant table actually exceeds the 10 M-row pganalyze threshold or shows planning-time regression in `pg_stat_statements`.
5. **Once Path A is green, remove `continue-on-error: true`** and reinstate the authoritative gate (per Atlas + Flyway 2024-2026 consensus that informational gates erode quickly).

---

## Citations (Markdown hyperlinks, year-tagged)

### Primary (≤5 years)
- [pgpartman/pg_partman README (2024-2025)](https://github.com/pgpartman/pg_partman)
- [pgpartman/pg_partman#842 — schema "partman" does not exist (2025)](https://github.com/pgpartman/pg_partman/issues/842)
- [Crunchy Data — pg_partman latest docs (2025)](https://access.crunchydata.com/documentation/pg-partman/latest/pg_partman/)
- [dbsystel/postgresql-partman-container (Apache-2.0, nightly, 2024-2025)](https://github.com/dbsystel/postgresql-partman-container)
- [Atlas FAQ — Managing PostgreSQL Extensions (2024)](https://atlasgo.io/faq/manage-extension-only)
- [Atlas v0.20 announcement — Postgres Extensions (March 2024)](https://atlasgo.io/blog/2024/03/18/atlas-v-0-20)
- [Redgate — Managing PostgreSQL Extensions Using Flyway (2024)](https://www.red-gate.com/hub/product-learning/flyway/managing-postgresql-extensions-using-flyway)
- [PostgreSQL.org — Flyway Community Drift Check released (2024)](https://www.postgresql.org/about/news/flyway-community-drift-check-released-2970/)
- [pganalyze — Partitioning in Postgres and the risk of high partition counts (2023)](https://pganalyze.com/blog/5mins-postgres-partitioning)
- [Tackling a 1.5 TB Table with pg_partman — Wemolo case (2024)](https://medium.com/@syedfahadkhalid93/tackling-a-1-5tb-table-near-zero-downtime-partitioning-in-postgresql-using-pg-partman-7e2ae55b9b4f)
- [Finextra — pg_partman deep dive (2024)](https://www.finextra.com/blogposting/30616/a-technical-deep-dive-into-pgpartman-simplifying-partition-lifecycle-management-at-scale)
- [Tiger Data — pg_partman vs. hypertables (2024)](https://www.tigerdata.com/learn/pg_partman-vs-hypertables-for-postgres-partitioning)
- [Supabase Discussion #37986 — Add pg_partman to preconfigured extensions (Oct 2025)](https://github.com/orgs/supabase/discussions/37986)
- [Supabase Discussion #14506 — Can Not Install Extension pg_partman](https://github.com/orgs/supabase/discussions/14506)
- [supabase/cli#3688 — Customize Postgres image in config.toml (June 2025, OPEN)](https://github.com/supabase/cli/issues/3688)
- [Supabase CLI Config docs (2025)](https://supabase.com/docs/guides/cli/config)
- [AWS RDS — Managing PG partitions with pg_partman (2024)](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL_Partitions.html)
- [Neon docs — pg_partman extension (2025)](https://neon.com/docs/extensions/pg_partman)
- [Crunchy Data — Postgres + Citus + Partman IoT (2024)](https://www.crunchydata.com/blog/postgres-citus-partman-your-iot-database)

### Secondary
- [PostgreSQL 18 docs — Table Partitioning (2025)](https://www.postgresql.org/docs/current/ddl-partitioning.html)
- [supabase/postgres Docker Hub](https://hub.docker.com/r/supabase/postgres)

### Flagged > 5 years
- None used as primary; all consensus claims come from 2023-2026 sources.
