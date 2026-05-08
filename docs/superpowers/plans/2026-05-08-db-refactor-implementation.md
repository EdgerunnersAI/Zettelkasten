# DB Refactor Implementation Plan (rev. 2 — post-audit)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [docs/superpowers/specs/2026-05-08-db-refactor-design.md](../specs/2026-05-08-db-refactor-design.md) (rev 2)
**Audit report:** Code-reviewer audit returned 12 BLOCKERs + 29 MAJORs on rev 1; all addressed in this rev.
**Executor instructions:** [docs/db-v2/executor-prompt.md](../../db-v2/executor-prompt.md) (under-400-word brief for the engine)
**Dashboard format:** [docs/db-v2/dashboard-sample.md](../../db-v2/dashboard-sample.md) and [docs/db-v2/dashboard-sample.svg](../../db-v2/dashboard-sample.svg)

**Goal:** Migrate the entire production Supabase schema to the new 6-schema layout (core/content/kg/rag/pipelines/billing) with canonical-content dedup, workspace tenancy, JWT-claim RLS, halfvec(768), unified usage_events, and a data-driven scorer registry — as a single weekend big-bang cutover with same-day PITR rollback **and 14-day legacy-table retention**.

**Architecture:** Six Postgres schemas with downward-only FK direction. Identity via Supabase `auth.users` → `core.profiles` (no Render legacy). RLS via JWT custom claim `workspace_ids[]` (no EXISTS-against-mapping anti-pattern; reference: [Supabase RLS Performance](https://supabase.com/docs/guides/troubleshooting/rls-performance-and-best-practices-Z5Jjwv)). Content stored once in `content.canonical_*`, referenced via per-workspace overlay. RAG retrieval reads weights from a four-table scorer registry refreshed via `pg_notify` over a **direct-port** (5432) Postgres connection, NOT pgbouncer (transaction-pool mode breaks LISTEN — see [pgbouncer features doc](https://www.pgbouncer.org/features.html)). ANN goes through a SECURITY DEFINER RPC `content.search_chunks()` that authorizes the caller's workspace. Usage events in one partitioned fact table via [pg_partman](https://github.com/pgpartman/pg_partman) ≥ 5.0.

**Tech Stack:** Supabase Postgres 15+, pgvector ≥ 0.8 (halfvec, iterative_scan — [release](https://www.postgresql.org/about/news/pgvector-080-released-2952/)), pg_partman ≥ 5, pg_cron, Python 3.12, FastAPI + asyncpg, **supabase-py ≥ 2.7.0** (required for `client.schema(...)`), pytest + pytest-asyncio + pytest-httpx.

**Phases (with audit-fix tasks called out inline):**

- **Phase 0** — Pre-flight (NOW EXPANDED for audit findings)
- **Phase 1** — Schema DDL applied to dev (idempotent, NO HNSW yet — audit B.7)
- **Phase 2** — Python repository layer with race-safe upserts (audit C.5)
- **Phase 3** — Scorer registry adapter with direct-port LISTEN + post-fork init (audit C.3, F.2)
- **Phase 4** — API + retrieval code paths
- **Phase 5** — Backfill scripts with **per-step verification gates** (audit D.3)
- **Phase 6** — Test coverage including the gaps the audit identified
- **Phase 7** — Cutover runbook (with concrete `drop_old_schemas.sql` + maintenance-mode test)
- **Phase 8** — Cutover execution
- **Phase 9** — Post-cutover cleanup (legacy retained 14 days, NOT 7 — audit D.2)

---

## File Structure

### New SQL files (under `supabase/website/_v2/`)

| File | Responsibility |
|---|---|
| `_v2/00_extensions.sql` | Extensions (`pgcrypto`, `vector`, `pg_partman`, **`pg_cron`** — audit F.1) + `core` schema + `core._migrations_applied` bootstrap (audit I.1) |
| `_v2/01_core_schema.sql` | `core.profiles` (with `allowlist_status` — audit I.2), workspaces, members, partitioned `usage_events`, aggregates, quotas, soft_delete_queue, **fixed `core.jwt_workspace_ids()`** (audit B.1), **typed `core.consume_quota()` RPC** (audit C.2), allowlist trigger, JWT-sync trigger, auto-personal-workspace trigger |
| `_v2/02_content_schema.sql` | `content.embedding_model_versions`, `canonical_zettels`, `canonical_chunks` (halfvec(768), **NO HNSW yet**), `workspace_zettels`, `workspace_chunk_membership` (**widened PK** — audit B.6), FTS trigger, **split soft-delete triggers** (audit B.4 + A.3 citation-integrity), **`content.search_chunks()` RPC** (audit B.5) |
| `_v2/03_kg_schema.sql` | `kg_nodes` (with `workspace_key` generated column + UNIQUE — audit B.2), `kg_edges` (preserves `shared_tag_label` — audit A.4), `chunk_node_mentions`, **authorized `kg.expand_subgraph()` RPC** (audit B.3) |
| `_v2/04_rag_schema.sql` | kastens, kasten_members (with **owner-can-grant trigger** — audit I), kasten_zettels, chat_sessions/messages (with workspace-consistency trigger; `retrieval_run_id` FK — audit A.2), retrieval_signal_weights, four scorer-registry tables, **idempotent `notify_pipeline_config_change`** (audit E.6) |
| `_v2/05_pipelines_schema.sql` | `pipeline_runs`, `pipeline_run_items` |
| `_v2/06_billing_schema.sql` | All `billing.pricing_*` rekeyed to `profile_id UUID FK`; new `billing.pricing_plan_entitlements`; rewrite `pricing_consume_entitlement(profile_id, feature, unit)` to read `core.usage_aggregates` |
| `_v2/07_partman_setup.sql` | `partman.create_parent('core.usage_events', ...)` **with idempotency guard** (audit D.6); `cron.schedule('partman_run_maintenance', ...)` |
| `_v2/08_rls_policies.sql` | RLS on every workspace-scoped table; `canonical_zettels`/`canonical_chunks` are **service-role-only** (reads via `content.search_chunks()` RPC) |
| `_v2/09_seed_scorer_registry.sql` | Seed 8 known scorers + v1 versions + per-environment config |
| `_v2/10_hnsw_indexes.sql` | **HNSW index on `content.canonical_chunks.embedding` — applied AFTER backfill** (audit B.7) |

### New Python files (under `website/core/supabase_v2/`)

| File | Responsibility |
|---|---|
| `client.py` | `get_v2_client()` (service-role), `get_v2_user_client(user_jwt)` for **RLS testing** (audit G.3); `parse_jwt_workspace_ids()` |
| `models.py` | Pydantic models — ~25 models matching DDL |
| `repositories/content_repository.py` | Race-safe `upsert_canonical_zettel` via `INSERT … ON CONFLICT … RETURNING id, (xmax = 0) AS was_new` (audit C.5); calls use `client.schema("content").table(...)` form (audit C.1) |
| `repositories/kg_repository.py`, `rag_repository.py`, `chat_repository.py`, `billing_repository.py`, `usage_events_repository.py` | Same pattern; uses `.schema(...).table(...)`; calls `core.consume_quota()` RPC for atomic debit |
| `features/rag_pipeline/scoring/registry_adapter.py` | THE ~150-line adapter. **Connection acquired from a separate asyncpg pool on port 5432** (NOT 6543 pgbouncer — audit C.3). 60s polling fallback. Initialized in **gunicorn `post_fork` hook** — audit F.2 |
| `features/rag_pipeline/scoring/registry_init.py` | Boot validator: fail-fast if any code-defined scorer missing from registry |

### Backfill scripts (under `ops/scripts/refactor_v2/`)

| File | Responsibility |
|---|---|
| `00_full_backfill.py` | Orchestrator. **Per-step verification gate** (audit D.3) requiring `--continue` between phases on cutover. |
| `01_backfill_profiles.py` through `08_recompute_signal_weights.py` | Per-domain backfills (see Phase 5 tasks) |
| `verify_backfill.py` | Post-backfill assertions |

### Tests

| File | Purpose |
|---|---|
| `tests/v2/integration/test_jwt_workspace_ids.py` | **NEW (audit B.1)**: unit-test the cast chain on a real Supabase JWT |
| `tests/v2/integration/test_canonical_dedup.py` | Two-user dedup happy path |
| `tests/v2/integration/test_canonical_concurrent_upsert.py` | **NEW (audit C.5, E.1)**: 10 parallel inserts of same URL → 1 canonical |
| `tests/v2/integration/test_kasten_sharing.py` | Sharing + role enforcement |
| `tests/v2/integration/test_jwt_rls.py` | **Uses `get_v2_user_client(jwt)`** (audit G.3), not service-role |
| `tests/v2/integration/test_canonical_chunks_direct_select_denied.py` | **NEW (audit E.3)**: SELECT on `content.canonical_chunks` directly returns 0 rows |
| `tests/v2/integration/test_search_chunks_rpc.py` | RPC works for authorized; raises for unauthorized |
| `tests/v2/integration/test_scorer_registry_adapter.py` | Boot, hot-reload via direct-port pg_notify, idempotent UPDATE skips notify (audit E.6) |
| `tests/v2/integration/test_quota_enforcement.py` | Race-test 5 parallel debits |
| `tests/v2/integration/test_soft_delete_reaper.py` | Reaper enqueue on soft-delete + DELETE; skips if cited (audit A.3); ignores if rereferenced |
| `tests/v2/integration/test_partman_future_partitions.py` | **NEW (audit E.2)**: `partman.run_maintenance` after time-shift creates next-month partition |
| `tests/v2/integration/test_eager_jwt_refresh.py` | **NEW (audit E.4)**: membership change → `auth.admin.update_user_by_id` reflects → re-issued JWT carries new array |
| `tests/v2/integration/test_halfvec_recall.py` | Recall@10 within 1% of vector(768) baseline |
| `tests/v2/integration/test_workspace_auto_create.py` | **NEW (audit E.7)**: profile insert → personal workspace + owner membership |
| `tests/v2/integration/test_full_pipeline_smoke.py` | End-to-end |

### Documentation

- `docs/db-v2/cutover-runbook.md` — operator's executable checklist
- `docs/db-v2/rollback-runbook.md` — same-day PITR + env-flag flip
- `docs/db-v2/post-cutover-monitoring.md` — first-24h KPIs and trip-wires
- `docs/db-v2/executor-prompt.md` — the under-400-word brief for the agent that runs this plan
- `docs/db-v2/dashboard-sample.md` + `dashboard-sample.svg` — execution-dashboard format

---

## Phase 0 — Pre-Flight (Days -7 to -1, before code)

### Task 0.1: ASK USER for explicit approval to provision new Supabase v2-dev project (audit F.4)

- [ ] **Step 1:** Surface the cost (additional Supabase Pro project ≈ $25/mo for the development window)
- [ ] **Step 2:** Wait for explicit user approval in chat. Do NOT proceed without it ([CLAUDE.md `feedback_anything_beyond_plan_needs_approval.md`](../../../CLAUDE.md))

### Task 0.2: Provision the Supabase v2-dev project

- [ ] **Step 1:** Create `zettelkasten-v2-dev` project, same region as prod
- [ ] **Step 2:** **Verify PITR add-on is enabled on the prod project too** (audit D.4). Document retention window
- [ ] **Step 3:** Capture `SUPABASE_V2_URL`, `SUPABASE_V2_SERVICE_ROLE_KEY`, `SUPABASE_V2_DATABASE_URL` (direct port 5432, NOT 6543 — audit C.3) into `.env.v2`. Wrap with `<private>...</private>` per CLAUDE.md
- [ ] **Step 4:** Verify Postgres 15+, `pgvector >= 0.8`, `pg_cron`, `pg_partman` extensions present

### Task 0.3: Configure PostgREST to expose v2 schemas (audit C.1, I.4)

- [ ] **Step 1:** In Supabase Dashboard → Database → API → "Exposed schemas," add: `core, content, kg, rag, pipelines, billing` (in addition to default `public, graphql_public`). Reference: [Supabase custom schemas](https://supabase.com/docs/guides/api/using-custom-schemas)
- [ ] **Step 2:** Save. Verify with `curl "$SUPABASE_V2_URL/rest/v1/?apikey=$ANON_KEY" | jq '.'` — schema list includes the six

### Task 0.4: Verify JWT custom-claim round-trip end-to-end (audit G.1)

- [ ] **Step 1:** Create a test user via `supabase.auth.admin.create_user(email='t1@test.com', password='...', user_metadata={}, app_metadata={'workspace_ids': ['00000000-0000-0000-0000-000000000001']})`
- [ ] **Step 2:** Sign in as that user; capture the access token JWT
- [ ] **Step 3:** Decode the JWT (jwt.io or Python `pyjwt`); confirm payload contains `app_metadata.workspace_ids: ["00000000-..."]`
- [ ] **Step 4:** Apply a temporary `00_extensions.sql` to v2-dev (just to get `auth.jwt()` available); call `SELECT core.jwt_workspace_ids();` while authenticated as that user (via supabase-py with the JWT)
- [ ] **Step 5:** Assert returns `{00000000-0000-0000-0000-000000000001}`. **If it doesn't, do NOT proceed** — investigate the claim path before any other work

### Task 0.5: Implement Caddy maintenance-mode (audit D.1)

**Files:** Modify `ops/caddy/Caddyfile`

- [ ] **Step 1:** Read current `ops/caddy/Caddyfile`
- [ ] **Step 2:** Add a maintenance-mode block:

```caddy
# At the top of the site block, before reverse_proxy:
@maintenance file /etc/caddy/maintenance.flag
respond @maintenance "Service is under maintenance. We'll be back shortly." 503 {
    close
}
@health path /api/health
reverse_proxy @health <upstream>   # keep health checks alive

# Existing routes follow
```

- [ ] **Step 3:** Deploy to the **staging droplet** (not prod), test:
  - `touch /etc/caddy/maintenance.flag && sudo systemctl reload caddy`
  - `curl https://staging-url` → 503
  - `curl https://staging-url/api/health` → 200
  - `rm /etc/caddy/maintenance.flag && sudo systemctl reload caddy` → back to normal
- [ ] **Step 4:** Commit: `git add ops/caddy/Caddyfile && git commit -m "ops: add maintenance-mode flag-file to Caddy"`

### Task 0.6: Test PITR restore on a throwaway project (audit D.4)

- [ ] **Step 1:** Insert sentinel row at T1, wait, insert another at T2
- [ ] **Step 2:** Trigger PITR restore to T1 via dashboard
- [ ] **Step 3:** Confirm only T1 row remains; document elapsed restore time as the rollback-time budget

### Task 0.7: Pin `supabase-py >= 2.7.0` (audit G.2)

- [ ] **Step 1:** `pip show supabase` — confirm version. If <2.7.0, update `ops/requirements.txt`: `supabase>=2.7.0,<3.0.0`
- [ ] **Step 2:** Test: `python -c "from supabase import create_client; c = create_client(URL, KEY); print(c.schema('public'))"` works without error
- [ ] **Step 3:** Commit: `git add ops/requirements.txt && git commit -m "deps: pin supabase>=2.7.0 for .schema() support"`

### Task 0.8: Stub the cutover runbook (finalized in Task 7.1)

**Files:** Create `docs/db-v2/cutover-runbook.md`, `docs/db-v2/rollback-runbook.md`, `docs/db-v2/post-cutover-monitoring.md`

- [ ] **Step 1:** Stub each with the spec §5 11-step list
- [ ] **Step 2:** Commit: `docs: cutover/rollback/monitoring runbook stubs`

### Task 0.9: Confirm bot does NOT dual-write to Supabase (audit G.4)

- [ ] **Step 1:** Read `telegram_bot/pipeline/orchestrator.py` and `telegram_bot/pipeline/writer.py`
- [ ] **Step 2:** Grep: `grep -rn "supabase\|kg_users\|kg_nodes" telegram_bot/`
- [ ] **Step 3:** Confirm: bot writes only to `KG_DIRECTORY` (filesystem) and via GitHub Contents API. **No Supabase writes.** Document finding in `docs/db-v2/scope-confirmation.md`
- [ ] **Step 4:** If bot DOES write to Supabase: STOP, escalate to user. Bot would need v2 too

---

## Phase 1 — Schema DDL applied to dev (Days 1-3)

### Task 1.0: Modify `apply_migrations.py --v2` to be bootstrap-safe (audit I.1)

**Files:** Modify `ops/scripts/apply_migrations.py`

- [ ] **Step 1:** Read current logic
- [ ] **Step 2:** Update v2 mode to: (a) connect; (b) try `INSERT INTO core._migrations_applied`; (c) on relation-not-found error, FIRST apply `00_extensions.sql` (which creates the table) and then retry; (d) afterwards apply 01-09 in order, recording each
- [ ] **Step 3:** Test: drop `core` schema in v2-dev, run `python ops/scripts/apply_migrations.py --v2`, confirm exits 0 and all 10 files recorded
- [ ] **Step 4:** Commit

### Task 1.1: Apply `00_extensions.sql` (audit I.1, F.1)

- [ ] **Step 1:** Write the file (full content from spec §4.0). Includes `CREATE EXTENSION IF NOT EXISTS pg_cron`.
- [ ] **Step 2:** Apply via `python ops/scripts/apply_migrations.py --v2 --target=v2-dev`
- [ ] **Step 3:** Verify all four extensions + `core._migrations_applied`
- [ ] **Step 4:** Commit

### Task 1.2: Apply `01_core_schema.sql` (audit B.1, C.2, I.2)

- [ ] **Step 1:** Write the file from spec §4.1. **Critically include the corrected `core.jwt_workspace_ids()` and the typed `core.consume_quota()` RPC**
- [ ] **Step 2:** Apply
- [ ] **Step 3: Write a unit test for `jwt_workspace_ids()` immediately:**

**Files:** Create `tests/v2/integration/test_jwt_workspace_ids.py`

```python
import pytest
import jwt as pyjwt
from website.core.supabase_v2.client import get_v2_user_client

@pytest.mark.asyncio
async def test_jwt_workspace_ids_parses_array(test_user_with_workspaces):
    profile_id, [w1, w2], jwt_str = test_user_with_workspaces  # fixture mints user + JWT

    # Decode locally to confirm the claim is in the JWT
    decoded = pyjwt.decode(jwt_str, options={"verify_signature": False})
    assert set(decoded["app_metadata"]["workspace_ids"]) == {str(w1), str(w2)}

    # Call from inside Postgres
    client = get_v2_user_client(jwt_str)
    result = client.rpc("jwt_workspace_ids", {}).execute()
    assert set(result.data) == {str(w1), str(w2)}

@pytest.mark.asyncio
async def test_jwt_workspace_ids_empty_for_anon():
    from website.core.supabase_v2.client import get_v2_anon_client
    client = get_v2_anon_client()
    result = client.rpc("jwt_workspace_ids", {}).execute()
    assert result.data == []
```

- [ ] **Step 4:** Run → PASS. **If it fails the audit B.1 fix is wrong** — block here until it passes.
- [ ] **Step 5:** Commit

### Task 1.3: Apply `02_content_schema.sql` WITHOUT HNSW (audit B.7)

- [ ] **Step 1:** Write the file from spec §4.2. **Verify HNSW index is NOT in this file** — it's in 10_hnsw_indexes.sql
- [ ] **Step 2:** Apply
- [ ] **Step 3:** Verify trigger split (audit B.4): `\d content.workspace_zettels` shows two triggers, one AFTER DELETE, one AFTER UPDATE OF deleted_at
- [ ] **Step 4:** Commit

### Tasks 1.4 through 1.10

Same pattern: write per-schema files from spec §4.3-§4.7 + RLS + seed registry + partman setup. Each commits separately.

- [ ] **1.4:** `03_kg_schema.sql` (audit B.2 generated column, B.3 authorization in expand_subgraph, A.4 shared_tag_label)
- [ ] **1.5:** `04_rag_schema.sql` (audit A.2 retrieval_run_id FK, idempotent pg_notify, kasten owner-grant trigger, chat_messages workspace consistency trigger)
- [ ] **1.6:** `05_pipelines_schema.sql`
- [ ] **1.7:** `06_billing_schema.sql` (rewritten `pricing_consume_entitlement` reading from `core.usage_aggregates`)
- [ ] **1.8:** `07_partman_setup.sql` with idempotency guards (audit D.6):
  ```sql
  DO $$ BEGIN
    PERFORM partman.create_parent(...);
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = '42710' THEN  -- duplicate_object
      RAISE NOTICE 'partman.create_parent already configured';
    ELSE RAISE;
    END IF;
  END $$;
  ```
- [ ] **1.9:** `08_rls_policies.sql` — `canonical_chunks` + `canonical_zettels` get **service-role-only** policies (no authenticated SELECT). Authenticated reads go through `content.search_chunks()` RPC
- [ ] **1.10:** `09_seed_scorer_registry.sql`
- [ ] **1.11:** `10_hnsw_indexes.sql` — file present in repo but **NOT applied here**; applied at cutover Step 5 only

---

## Phase 2 — Python Repository Layer (Days 4-7)

### Task 2.1: Scaffold `website/core/supabase_v2/` with two-flavor client (audit G.3)

**Files:** Create `__init__.py`, `client.py`

- [ ] **Step 1:** Create the package
- [ ] **Step 2:** Write `client.py` with **three constructors**:

```python
from supabase import create_client, Client

def get_v2_client() -> Client:
    """Service-role client. Bypasses RLS. For server-internal writes only."""
    s = get_settings()
    return create_client(s.supabase_v2_url, s.supabase_v2_service_role_key)

def get_v2_anon_client() -> Client:
    """Anon client. Subject to RLS as 'anon' role."""
    s = get_settings()
    return create_client(s.supabase_v2_url, s.supabase_v2_anon_key)

def get_v2_user_client(user_jwt: str) -> Client:
    """User-JWT client for RLS testing. Subject to RLS as 'authenticated' with the user's claims."""
    s = get_settings()
    c = create_client(s.supabase_v2_url, s.supabase_v2_anon_key)
    c.postgrest.auth(user_jwt)
    return c
```

- [ ] **Step 3:** Add settings field `supabase_v2_anon_key` and `supabase_v2_database_url` (port 5432 direct, NOT pooled — audit C.3)
- [ ] **Step 4:** Commit

### Task 2.2: Pydantic models (~25 models)

Same as rev 1 Task 2.2.

### Task 2.3: Test infrastructure with asyncpg-driven fixture (audit C.8)

**Files:** Create `tests/v2/conftest.py`

- [ ] **Step 1:** Replace fictional `client.rpc("execute_sql", ...)` with **direct asyncpg connection** for fixture truncates:

```python
import asyncpg

@pytest.fixture
async def fresh_v2_db():
    """Truncate all workspace-scoped tables via direct asyncpg."""
    conn = await asyncpg.connect(get_settings().supabase_v2_database_url)
    await conn.execute("""
        TRUNCATE
          content.workspace_chunk_membership, content.workspace_zettels,
          content.canonical_chunks, content.canonical_zettels,
          kg.chunk_node_mentions, kg.kg_edges, kg.kg_nodes,
          rag.chat_messages, rag.chat_sessions,
          rag.kasten_zettels, rag.kasten_members, rag.kastens,
          rag.retrieval_signal_weights,
          pipelines.pipeline_run_items, pipelines.pipeline_runs,
          core.usage_aggregates, core.quotas,
          core.workspace_members, core.workspaces, core.profiles
        CASCADE;
    """)
    # Also delete corresponding auth.users rows (since profiles FK them)
    await conn.execute("DELETE FROM auth.users WHERE email LIKE '%@test.com';")
    await conn.close()
    yield get_v2_client()

@pytest.fixture
async def test_user_with_workspaces():
    """Mint a real auth.users row + profile + 2 personal-then-secondary workspaces, return JWT."""
    client = get_v2_client()
    # auth.admin.create_user creates the row + can set app_metadata
    user = client.auth.admin.create_user({
        "email": f"u-{uuid4().hex[:8]}@test.com",
        "password": "x" * 16,
        "email_confirm": True,
    })
    # Trigger creates profile + personal workspace; we add a second workspace
    profile_id = UUID(user.user.id)
    # ... (create second workspace, add membership, etc.)
    # Sign in to get JWT
    session = client.auth.sign_in_with_password({"email": user.user.email, "password": "x"*16})
    yield profile_id, [w1, w2], session.session.access_token
    # Cleanup
    client.auth.admin.delete_user(user.user.id)
```

- [ ] **Step 2:** Add asyncpg to dev deps: `pip install asyncpg`; update `ops/requirements-dev.txt`
- [ ] **Step 3:** Commit

### Task 2.4: TDD `ContentRepository.upsert_canonical_zettel` — race-safe (audit C.5, E.1)

**Files:** Create `repositories/content_repository.py`, `tests/v2/integration/test_canonical_concurrent_upsert.py`

- [ ] **Step 1:** Write the **concurrent** failing test:

```python
@pytest.mark.asyncio
async def test_upsert_canonical_zettel_10_parallel_idempotent(fresh_v2_db):
    repo = ContentRepository(fresh_v2_db)
    body = "shared body"
    create = CanonicalZettelCreate(
        normalized_url="https://example.com/x",
        content_hash=hashlib.sha256(body.encode()).digest(),
        source_type="web", body_md=body,
    )
    results = await asyncio.gather(*[repo.upsert_canonical_zettel(create) for _ in range(10)])
    canonical_ids = {r[0] for r in results}
    was_news = [r[1] for r in results]
    assert len(canonical_ids) == 1, f"expected 1 canonical, got {len(canonical_ids)}"
    assert sum(was_news) == 1, "exactly one caller should see was_new=True"
```

- [ ] **Step 2:** Implement using `INSERT … ON CONFLICT DO UPDATE … RETURNING id, (xmax = 0) AS was_new`:

```python
async def upsert_canonical_zettel(self, create: CanonicalZettelCreate) -> tuple[UUID, bool]:
    """Race-safe upsert. xmax = 0 trick: row was just inserted iff xmax is the zero txid.
    Reference: https://stackoverflow.com/a/39204667 ; https://www.postgresql.org/docs/current/ddl-system-columns.html"""
    sql = """
        INSERT INTO content.canonical_zettels
            (normalized_url, content_hash, source_type, title, body_md, publication_date, source_metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (normalized_url, content_hash) DO UPDATE SET
            -- no-op update so we get a row back; updated_at unchanged
            normalized_url = EXCLUDED.normalized_url
        RETURNING id, (xmax = 0) AS was_new;
    """
    # supabase-py doesn't expose raw SQL → use asyncpg here, OR define an RPC
    # Cleanest: define core.upsert_canonical_zettel RPC.
    result = self.client.rpc("upsert_canonical_zettel", {
        "p_normalized_url": create.normalized_url,
        "p_content_hash": create.content_hash.hex(),
        ...
    }).execute()
    row = result.data[0]
    return UUID(row["id"]), row["was_new"]
```

(Add corresponding RPC to `02_content_schema.sql`.)

- [ ] **Step 3:** Run → PASS
- [ ] **Step 4:** Commit

### Task 2.5: TDD `upsert_canonical_chunks` with halfvec round-trip

Same as rev 1 Task 2.5 but uses `client.schema("content").table("canonical_chunks")` form (audit C.1). Verifies halfvec serialization through supabase-py.

### Task 2.6 - 2.11: Remaining repository TDDs

Same as rev 1, with the consistent fixes:
- All calls use `client.schema("...").table("...")` form (audit C.1)
- `consume_quota_atomic` calls the typed `core.consume_quota` RPC (audit C.2)
- `BillingRepository` uses `profile_id` everywhere
- The race-test for `consume_quota` runs 5-parallel debits at remaining=3 → exactly 3 succeed

---

## Phase 3 — Scorer Registry Adapter (Days 8-9)

### Task 3.1: TDD `RegistryAdapter.load()` (snapshot from DB)

Same as rev 1 Task 3.1.

### Task 3.2: TDD `pg_notify` hot-reload — DIRECT-PORT connection (audit C.3)

**Files:** Modify `registry_adapter.py`, `test_scorer_registry_adapter.py`

- [ ] **Step 1:** Write a failing test that flips a weight in the DB and asserts the adapter picks it up within 500 ms

- [ ] **Step 2:** **Implement using a dedicated asyncpg connection on port 5432** (NOT 6543):

```python
# In RegistryAdapter:
async def start_listening(self, db_url_direct_port: str) -> None:
    """Open a dedicated asyncpg connection on Postgres direct port (5432).
    pgbouncer transaction-pool mode (port 6543) silently drops LISTEN — see
    https://www.pgbouncer.org/features.html "Not supported".
    Caller MUST pass the direct-port URL, NOT the pooled URL.
    """
    if "6543" in db_url_direct_port:
        raise ValueError("RegistryAdapter requires direct Postgres port (5432), not pgbouncer 6543")
    self._listener_conn = await asyncpg.connect(db_url_direct_port)
    await self._listener_conn.add_listener("retrieval_pipeline_config_change", self._on_notify)
    # Also start a 60s polling fallback in case the LISTEN connection dies
    self._poll_task = asyncio.create_task(self._poll_loop())

async def _poll_loop(self) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await self.load()
        except Exception:
            logger.exception("RegistryAdapter poll-fallback reload failed")

async def _on_notify(self, conn, pid, channel, payload) -> None:
    if payload != self.environment:
        return
    try:
        await self.load()
    except Exception:
        logger.exception("RegistryAdapter on-notify reload failed")
```

- [ ] **Step 3:** Run → PASS
- [ ] **Step 4:** Commit

### Task 3.3: Idempotent-NOTIFY test (audit E.6)

```python
@pytest.mark.asyncio
async def test_registry_no_notify_on_unchanged_update(fresh_v2_db_with_registry, asyncpg_listener):
    """An UPDATE that doesn't change values should NOT fire pg_notify."""
    received = []
    async def cb(conn, pid, channel, payload): received.append(payload)
    await asyncpg_listener.add_listener("retrieval_pipeline_config_change", cb)

    # Re-set the same value
    fresh_v2_db_with_registry.schema("rag").table("retrieval_pipeline_config").update(
        {"weight": 1.0}
    ).eq("environment", "prod").eq("scorer_name", "anti_magnet").execute()
    await asyncio.sleep(0.2)
    assert received == []
```

- [ ] Run, commit

### Task 3.4: Boot validator + post-fork init (audit F.2)

**Files:** Create `registry_init.py`, modify `website/app.py` or `website/main.py` for **gunicorn `post_fork` hook**

- [ ] **Step 1:** Write the validator (same as rev 1)
- [ ] **Step 2:** Wire into gunicorn `post_fork` hook (NOT FastAPI lifespan, which runs pre-fork under `--preload`):

**Files:** Create `ops/gunicorn_conf.py`

```python
"""Gunicorn config: initializes RegistryAdapter in each worker post-fork.
With --preload, lifespan runs in the master process before workers fork; the
master's adapter snapshot is COW-shared but pg_notify listeners are not
inherited cleanly. Initialize per-worker.
"""
def post_fork(server, worker):
    import asyncio
    from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter
    from website.features.rag_pipeline.scoring.registry_init import validate_registry_completeness, EXPECTED_SCORERS
    from website.core.supabase_v2.client import get_v2_client
    from telegram_bot.config.settings import get_settings

    async def _init():
        client = get_v2_client()
        await validate_registry_completeness(client, expected_scorers=EXPECTED_SCORERS)
        adapter = RegistryAdapter(client, environment=get_settings().app_environment)
        await adapter.load()
        await adapter.start_listening(get_settings().supabase_v2_database_url)
        worker.app.state.registry_adapter = adapter

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_init())
```

- [ ] **Step 3:** Update Dockerfile / startup to use this `gunicorn_conf.py`
- [ ] **Step 4:** Commit

---

## Phase 4 — API + Retrieval Updates (Days 10-13)

### Task 4.1: Update `/api/summarize` to canonical-then-overlay

Same as rev 1 Task 4.1. Use `client.schema(...).table(...)` form.

### Task 4.2: ANN through `content.search_chunks()` RPC (audit B.5)

**Files:** Modify `website/features/rag_pipeline/retrieval/dense.py` (or equivalent)

- [ ] **Step 1:** Replace direct `SELECT … FROM content.canonical_chunks ORDER BY embedding <=> $1` with `client.rpc('search_chunks', {p_workspace_id: ..., p_query_embedding: vec, p_limit: 32})`
- [ ] **Step 2:** Verify cross-workspace denial: a JWT for w1 calling `search_chunks(p_workspace_id=w2)` raises `unauthorized` (42501)
- [ ] **Step 3:** Commit

### Task 4.3: Update `hybrid.py` to read RegistryAdapter

Same as rev 1.

### Task 4.4: Per-scorer registry reads

Same as rev 1.

### Task 4.5: User-facing error sanitization (audit F.3)

**Files:** Modify FastAPI exception handler

- [ ] **Step 1:** Add a global exception handler that logs the full error server-side but returns a generic 500 with `{"error": "internal server error", "request_id": "..."}` to the client. Never expose `RuntimeError` messages, scorer names, etc., per [CLAUDE.md `feedback_no_infra_disclosure.md`]
- [ ] **Step 2:** Test with a deliberately-broken request → assert response body has no scorer/schema names
- [ ] **Step 3:** Commit

### Task 4.6: Rewrite `recompute_signal_weights.py` + Razorpay webhook profile lookup

Same as rev 1.

---

## Phase 5 — Backfill Scripts WITH PER-STEP GATES (Days 14-17, audit D.3)

### Task 5.0: Backfill orchestrator with verification gates between phases

**Files:** Create `ops/scripts/refactor_v2/00_full_backfill.py`

```python
"""Full v2 backfill orchestrator.
Runs phases 01-08 in order. Between each, runs the phase's verification.
Aborts on any failure; requires --continue flag to skip a gate (operator override).
"""
PHASES = [
    ("01_backfill_profiles", "verify_profiles"),
    ("02_backfill_canonical_content", "verify_canonical"),
    ("03_backfill_kg", "verify_kg"),
    ("04_backfill_rag", "verify_rag"),
    ("05_backfill_pipelines", "verify_pipelines"),
    ("06_backfill_billing", "verify_billing"),
    ("07_backfill_usage_events", "verify_usage_events"),
    ("08_recompute_signal_weights", "verify_signal_weights"),
]

async def run(args):
    for module_name, verify_name in PHASES:
        print(f"━━━ Running {module_name} ━━━")
        await import_and_run(module_name)
        print(f"  ✓ {module_name} complete")
        result = await import_and_run_verify(verify_name)
        if not result.ok:
            print(f"  ✗ {verify_name} FAILED: {result.message}")
            if not args.continue_:
                sys.exit(2)
            print("    --continue specified; proceeding")
        else:
            print(f"  ✓ {verify_name} passed")
```

- [ ] Each `verify_X` function asserts row counts, key invariants, no orphan FKs
- [ ] Run end-to-end against an empty source; commit

### Tasks 5.1 - 5.9: Per-domain backfills

Same as rev 1, with key changes:
- **Backfill canonical content (5.3):** explicitly use `embedding::halfvec` direct SQL cast (audit C.6); document "no Gemini re-call"
- **Backfill billing (5.7):** fail-fast with explicit log row on unresolvable Razorpay subscriber → profile mappings
- **Backfill profiles (5.2):** **ALSO seed `auth.users` rows for the v2-dev project from prod** (audit A.1) using `auth.admin.create_user`; OR, in dev, drop the `auth.users` FK temporarily — document the chosen path

### Task 5.10: Comprehensive verification

Same as rev 1.

---

## Phase 6 — Test Coverage with audit-identified gaps (Days 18-19)

### Task 6.0: Add the 7 audit-mandated tests

| Test file | Audit | Covers |
|---|---|---|
| `test_jwt_workspace_ids.py` | B.1 | Cast chain returns correct array; empty for anon |
| `test_canonical_concurrent_upsert.py` | C.5, E.1 | 10 parallel inserts → 1 canonical, 1 was_new=True |
| `test_canonical_chunks_direct_select_denied.py` | E.3 | SELECT `content.canonical_chunks` directly returns 0 rows for authenticated user |
| `test_search_chunks_rpc.py` | B.5 | Authorized RPC returns rows; unauthorized raises |
| `test_partman_future_partitions.py` | E.2 | After `partman.run_maintenance` future month partition exists |
| `test_eager_jwt_refresh.py` | E.4 | Membership change → `auth.admin.update_user_by_id` reflects → re-issued JWT carries new array |
| `test_workspace_auto_create.py` | E.7 | Profile insert → personal workspace + owner membership |
| `test_registry_idempotent_notify.py` | E.6 | UPDATE with same values doesn't fire pg_notify |

### Tasks 6.1 - 6.5: Cross-cutting integration tests

Same as rev 1 (sharing flows, JWT-RLS denial, soft-delete reaper with citation-integrity check, halfvec recall, full pipeline smoke).

---

## Phase 7 — Cutover & Rollback Runbooks (Day 20)

### Task 7.1: Concrete cutover runbook (audit D.5)

Same as rev 1 BUT:
- Add **Step 5: Apply `10_hnsw_indexes.sql`** AFTER backfill (audit B.7)
- Add **Step 9: Rename old tables `_legacy_<name>`, do NOT DROP** for 14 days (audit D.2)
- Concrete rename script:

```sql
-- Renamed (NOT dropped); keep for 14 days as rollback safety net.
ALTER TABLE kg_users RENAME TO _legacy_kg_users;
ALTER TABLE kg_nodes RENAME TO _legacy_kg_nodes;
ALTER TABLE kg_links RENAME TO _legacy_kg_links;
ALTER TABLE kg_node_chunks RENAME TO _legacy_kg_node_chunks;
ALTER TABLE rag_sandboxes RENAME TO _legacy_rag_sandboxes;
ALTER TABLE rag_sandbox_members RENAME TO _legacy_rag_sandbox_members;
ALTER TABLE chat_sessions RENAME TO _legacy_chat_sessions;
ALTER TABLE chat_messages RENAME TO _legacy_chat_messages;
ALTER TABLE summary_batch_runs RENAME TO _legacy_summary_batch_runs;
ALTER TABLE summary_batch_items RENAME TO _legacy_summary_batch_items;
-- (continue for every legacy table)
```

- Add **post-cutover Day +14: drop `_legacy_*` tables** as a separate scheduled step

### Task 7.2: Rollback runbook with explicit user-comms (audit F.5)

Add: "Step 6 — Communicate with users via Telegram + status page about possible 30-min capture-loss window."

### Task 7.3: Maintenance-mode pre-flight test (audit D.1)

- [ ] Day before cutover: run the Caddy maintenance-mode toggle on staging; verify 503 on all routes except `/api/health`

---

## Phase 8 — Cutover Execution

### Task 8.1: Execute the runbook

Same as rev 1 Task 8.1.

### Task 8.2: Post-cutover verify

Same.

---

## Phase 9 — Post-Cutover Cleanup (audit D.2)

### Task 9.1: Day +14: drop `_legacy_*` tables

- [ ] **Step 1:** Verify 14-day stable production
- [ ] **Step 2:** `psql -c "DROP TABLE _legacy_kg_users, _legacy_kg_nodes, … CASCADE;"`
- [ ] **Step 3:** Drop the v2-dev Supabase project (no longer needed)

### Task 9.2: Rename `supabase_v2/` → `supabase/db/`

(NOT `supabase_kg/` as in rev 1 — the new package owns more than KG; audit I MINOR.)

### Task 9.3: Decommission legacy cron + save mem-vault observation

Same as rev 1.

---

## Self-Review

**1. Spec coverage:**

| Spec section | Plan task |
|---|---|
| §3 schema split | Phase 1 |
| §4.0 _migrations_applied bootstrap | Task 1.0, 1.1 |
| §4.1 jwt_workspace_ids fix + consume_quota RPC | Task 1.2 + test_jwt_workspace_ids.py |
| §4.1 allowlist | Task 1.2 |
| §4.2 split soft-delete triggers | Task 1.3 + test_soft_delete_reaper.py |
| §4.2 search_chunks RPC | Task 1.3 + 4.2 + test_search_chunks_rpc.py |
| §4.2 widened workspace_chunk_membership PK | Task 1.3 |
| §4.3 generated column UNIQUE | Task 1.4 |
| §4.3 expand_subgraph auth check | Task 1.4 |
| §4.4 idempotent pg_notify | Task 1.5 + test_registry_idempotent_notify.py |
| §4.4 kasten owner-grant trigger | Task 1.5 + test_kasten_sharing.py |
| §4.7 HNSW post-backfill | Task 1.11 + cutover Step 5 |
| §5 cutover sequence | Task 7.1 + 8.1 |
| §6 ~150-line registry adapter, direct-port LISTEN, post-fork | Task 3.1-3.4 |
| §6 Caddy maintenance-mode | Task 0.5 |
| §6 supabase-py >= 2.7.0 | Task 0.7 |

All audit findings (12 BLOCKER + 29 MAJOR) trace to a task. ✅

**2. Placeholder scan:** No "TBD" / "TODO" / "implement later".

**3. Type consistency:** `RegistryAdapter` API stable across phases. `consume_quota` RPC signature (`uuid, text, text, timestamptz`) consistent across spec §4.1, Phase 2 repo, and test. `client.schema("...").table("...")` form used uniformly.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-08-db-refactor-implementation.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks. Best for a 50+-task multi-phase plan; preserves the main-context window.

**2. Inline Execution** — execute tasks in this session with checkpoints.

**Which approach?**
