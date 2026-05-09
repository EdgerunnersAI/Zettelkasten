# Website-Features v2 Purge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec parents:**
- [docs/superpowers/specs/2026-05-08-db-refactor-design.md](../specs/2026-05-08-db-refactor-design.md) (rev 2 with audit fixes)
- [docs/superpowers/plans/2026-05-08-db-refactor-implementation.md](2026-05-08-db-refactor-implementation.md) (Pass-0 plan; this plan continues from where it stopped)

**Hard rules carried over (non-negotiable):**
- `~/.claude/projects/.../memory/feedback_pricing_module_authority.md` — pricing module is operator-defined per `docs/research/pricing1.md`. NEVER seed `billing.pricing_plan_entitlements`, NEVER alter `billing.pricing_consume_entitlement` semantics, NEVER invent plan names or auto-subscribe. 402 quota_exhausted is correct default. Read `pricing1.md` BEFORE touching anything billing.
- `feedback_anything_beyond_plan_needs_approval.md` — anything not in this plan = new decision = explicit chat approval first.
- `feedback_no_infra_disclosure.md` — never expose model name, tokens, latency, scores, query_class, etc. in user-facing UI.
- `feedback_progress_bar_mode.md` + `feedback_dashboard_mode_always.md` — multi-step execution is dashboard-only.

**Goal:** Move every remaining production code path off the legacy `supabase_kg` repository and the `public.kg_*` / `public.rag_*` / `public.chat_*` tables onto the v2 schemas (`core`, `content`, `kg`, `rag`, `pipelines`, `billing`). Verify each module via TDD + an end-to-end exerciser. Retire dead surfaces. Drop the old tables only after explicit operator approval. Honour the existing pricing module without modification.

**Architecture invariants (no operator decisions to make here — these are already locked):**
- v2 dual-path is the transition pattern: when `use_supabase_v2()` returns True AND `user_sub` is a Supabase auth UUID, route to v2 repos; otherwise fall back to v1. `persist.py` already exhibits this pattern correctly — copy it.
- `get_v2_client()` is the canonical client factory for v2 paths; `get_supabase_client()` from `supabase_kg.client` is legacy. Both target the SAME Supabase project today, so query results from old tables remain reachable via service-role until those tables are dropped.
- Read path migrations MUST preserve response shapes the frontend expects (`KGGraph`, `KGGraphNode`, `KGGraphLink` as defined in `website/core/graph_models.py`).
- Every v2 RPC and table the Python layer touches MUST be in `expected_schema.json` (drift gate is enforced by `apply_migrations.py --v2`). New schema artefacts go in a new migration file under `supabase/website/_v2/`.
- Tests + implementation move together. Mocked unit tests can use `unittest.mock`; integration tests use the v2 fixture pattern from `tests/unit/supabase_v2/test_repositories.py`.

**Tech stack:** Supabase Postgres 15+ pg 17.6 deployed, pgvector 0.8 (halfvec), pg_partman 5+, pg_cron, Python 3.12, FastAPI + asyncpg, supabase-py ≥ 2.7.0.

---

## File Structure (touched in this plan)

### v2 SQL additions

| File | Purpose |
|---|---|
| `supabase/website/_v2/13_v2_kasten_rpcs.sql` | New SECURITY DEFINER RPCs for the v2 RAG read path: `rag.search_signal_weights`, `rag.chunk_share_for_kasten`, `rag.bulk_add_to_kasten`, `rag.fetch_anchor_seeds_v2`. Each authorises caller via `core.jwt_workspace_ids()`. Replaces 4 legacy RPCs (`rag_kasten_chunk_counts`, `rag_resolve_entity_anchors`, `rag_one_hop_neighbours`, `rag_fetch_anchor_seeds`) with v2-shaped equivalents. NO behavioural change to retrieval — same scoring math, just over v2 tables. |
| `supabase/website/_v2/14_legacy_freeze.sql` | Mark legacy tables read-only via REVOKE + comment. NOT a drop. Drop is a separate operator-approved step in Phase 6. |

### Python files modified (Bucket B)

| File | Refactor target |
|---|---|
| `website/features/rag_pipeline/ingest/upsert.py` | Replace `kg_node_chunks` writes with `content.canonical_chunks` + `content.workspace_chunk_membership` via `ContentRepository.upsert_canonical_chunks` + `link_workspace_chunks`. Halfvec embedding cast preserved. |
| `website/features/rag_pipeline/memory/sandbox_store.py` | Replace `rag_sandboxes` CRUD with `rag.kastens` via new `RAGRepository` methods. Remove the nested `select("..., kg_nodes(...)")` PostgREST embed; use explicit JOIN through `kasten_zettels → workspace_zettels → canonical_zettels`. |
| `website/features/rag_pipeline/memory/session_store.py` | Replace `chat_sessions` + `chat_messages` CRUD with `rag.chat_sessions` + `rag.chat_messages`. workspace_id is REQUIRED on every insert (DB-level NOT NULL); pull from JWT claim. |
| `website/features/rag_pipeline/retrieval/hybrid.py` | Replace 3 legacy RPC calls with the v2 equivalents from `_v2/13_v2_kasten_rpcs.sql`. Embed reads switch to `content.search_chunks` via `ContentRepository`. |
| `website/features/rag_pipeline/retrieval/graph_score.py` | Replace `kg_usage_edges_agg` materialised-view read with `rag.retrieval_signal_weights` direct read. The recompute cron (`ops/scripts/recompute_signal_weights.py`) already targets this table. |
| `website/features/rag_pipeline/retrieval/chunk_share.py` | Replace `rag_kasten_chunk_counts` RPC with `rag.chunk_share_for_kasten` from new RPC file. |
| `website/features/rag_pipeline/retrieval/kasten_freq.py` | RETIRE. RES-2 in spec §1 documents that this prior is dead (floor=50 never crossed). Replace the file with a thin module-level no-op that returns 1.0 (multiplicative identity) so callers don't break. Delete the legacy table reference. |
| `website/features/summarization_engine/writers/supabase.py` | Replace `KGRepository.upsert_node` with `ContentRepository.upsert_canonical_zettel` + `add_workspace_overlay`. The summary text moves from `kg_nodes.summary` (jsonb) to `content.workspace_zettels.ai_summary` (text). |
| `website/features/user_pricing/repository.py` | Swap `from website.core.supabase_kg.client import is_supabase_configured` for the v2 equivalent (`is_v2_configured` from `supabase_v2.client`). NO change to `pricing_consume_entitlement` calls or any pricing logic. |
| `website/features/web_monitor/User_Activity.py` | Comment-only update (the file references `kg_users` only in docstrings). Re-point comments at `core.profiles`. No code change. |
| `website/api/nexus.py` | Swap `is_supabase_configured` import to v2 module; otherwise unchanged. Nexus tables already mapped to `pipelines.pipeline_runs` with `kind='nexus_ingest'` per spec §3. |
| `website/experimental_features/nexus/service/bulk_import.py`, `token_store.py`, `oauth_state.py` | Swap `get_supabase_client` for `get_v2_client`. Tables to read/write: `pipelines.pipeline_runs` for ingest history, plus a new `pipelines.nexus_provider_tokens` table (added in `13_v2_kasten_rpcs.sql`) that mirrors the existing `nexus_provider_accounts` shape. |
| `website/experimental_features/PageIndex_Rag/data_access.py` | RETIRE. Experimental file that directly queries `public.kg_users` + `public.kg_nodes`. The PageIndex_Rag module is not on any live route per `website/api/routes.py`. Delete the file; if the parent module breaks at import, replace its body with `raise NotImplementedError("PageIndex_Rag is retired pending v2 design")`. |

### Read-path API handler updates

| File | Change |
|---|---|
| `website/api/routes.py` `/api/graph` | Branch on `use_supabase_v2()`: v2 path queries `content.workspace_zettels` JOIN `content.canonical_zettels` + `kg.kg_edges` (workspace-scoped) and assembles a `KGGraph` payload. v1 path stays as today during transition. |
| `website/api/routes.py` `/api/me` | Branch: v2 path reads `core.profiles` directly via `CoreRepository.get_profile`. v1 path stays during transition. |
| `website/api/routes.py` POST `/api/zettels/{node_id}` (delete/update) | Route to `content.workspace_zettels` soft-delete (`deleted_at`) when in v2 mode. Reaper trigger handles canonical shred. |
| `website/api/sandbox_routes.py` (kasten CRUD) | Branch to `rag.kastens` + `rag.kasten_zettels` for create/list/add-zettel/delete. |
| `website/core/persist.py` (READ path) | Add `get_supabase_v2_scope_for_read(user_sub)` analog to the existing write-path scope helper. Used by `/api/graph` and `/api/zettels/list`. |

### Tests

Each Bucket-B file gets a paired test (modified or new). Same TDD cycle as the v2 unit tests in `tests/unit/supabase_v2/`.

| Test file | Covers |
|---|---|
| `tests/unit/rag_pipeline/test_ingest_upsert_v2.py` | Mock supabase v2 client; verify upsert lands canonical+membership |
| `tests/unit/rag_pipeline/test_sandbox_store_v2.py` | Kasten CRUD via v2 schema; JOIN shape |
| `tests/unit/rag_pipeline/test_session_store_v2.py` | chat_sessions/messages workspace_id required |
| `tests/unit/rag_pipeline/test_hybrid_v2_rpc.py` | New RPC names, param contracts, auth check |
| `tests/unit/rag_pipeline/test_graph_score_v2.py` | retrieval_signal_weights read shape |
| `tests/unit/rag_pipeline/test_chunk_share_v2.py` | New chunk_share RPC contract |
| `tests/unit/rag_pipeline/test_kasten_freq_retired.py` | Identity-1.0 return; no DB calls |
| `tests/unit/summarization_engine/test_writer_v2.py` | Writes land in workspace_zettels |
| `tests/unit/user_pricing/test_repository_v2_imports.py` | is_v2_configured swap; no pricing-logic regression |
| `tests/integration/v2/test_api_graph_v2.py` | E2E /api/graph returns workspace-scoped data |
| `tests/integration/v2/test_kasten_share_e2e.py` | Owner shares kasten → recipient role enforcement |
| `tests/integration/v2/test_pure_v2_writes.py` | Generalisation of `ops/scripts/verify_v2_e2e.py` |
| `tests/integration/v2/test_pricing_unmodified.py` | **Locks down pricing semantics**: asserts `pricing_consume_entitlement` returns false for missing subscription/entitlement (the documented correct behaviour). Fails CI if anyone re-introduces a default-to-free or seed. |

### Backfill scripts (existing — verify + run)

| File | Status |
|---|---|
| `ops/scripts/refactor_v2/00_full_backfill.py` | Already exists. Run end-to-end against current production data (1 user, 0 zettels — almost no-op) to verify code paths are alive. |
| `ops/scripts/refactor_v2/02_backfill_canonical_content.py` | Already exists. Verify halfvec cast path. |
| `ops/scripts/refactor_v2/verify_backfill.py` | Run after every backfill phase; HARD FAIL on any assertion. |

### Cutover & cleanup

| File | Purpose |
|---|---|
| `docs/db-v2/cutover-runbook.md` | Already exists; expand with the precise drop list from this plan's Phase 6. |
| `supabase/website/_v2/15_drop_legacy_tables.sql` | DESTRUCTIVE migration that DROPs every old `public.kg_*`, `public.rag_*`, `public.chat_*`, `public.kg_usage_edges*`, `public.kg_kasten_node_freq`, `public.summary_batch_*`, `public.nexus_*` table. **NEVER applied without explicit operator approval per change.** Includes a 14-day-soak guard that fails the migration if `(now() - cutover_timestamp_in_audit_log) < INTERVAL '14 days'`. |

---

## Pre-Phase-0 Amendments (folded in from bulletproof audit 2026-05-09)

The original Phase 0 was insufficient. These amendments add 31 BLOCKER+MAJOR items the bulletproof audit surfaced. Read every amendment before executing the corresponding phase.

### Amendment 0.0 — Clean working tree (BLOCKER, audit L.7)

- [ ] `git status` MUST show clean (modulo the documented untracked dirs: `.claude/`, `.deepeval/`, eval cache, etc.). Untracked plan/spec edits must be committed or stashed before Phase 0 starts.

### Amendment 0.4 — Test infrastructure (BLOCKER, audit C.1, G.1, G.2, H.2)

- [ ] **Create** `tests/v2/__init__.py`, `tests/v2/fixtures/__init__.py`, `tests/v2/fixtures/users.py` with `mint_test_user_with_workspaces(*, workspace_count: int = 1) -> tuple[UUID, list[UUID], str]` that:
  1. Calls `supabase.auth.admin.create_user(email=f"e2e-{uuid4().hex[:8]}@test.com", password="x"*16, email_confirm=True)`
  2. The `core.handle_new_auth_user` trigger creates a profile + personal workspace + member row
  3. If `workspace_count > 1`: insert additional workspaces directly + add the profile as owner-member
  4. Sign in via `client.auth.sign_in_with_password` to get a JWT
  5. Return `(profile_id, [workspace_ids], jwt)`
- [ ] **Create** `tests/integration/v2/__init__.py` and `tests/integration/v2/conftest.py` with:
  - `asyncpg_pool` fixture: `await asyncpg.create_pool(SUPABASE_DATABASE_URL)` — direct port 5432
  - re-export of `mint_test_user_with_workspaces`
  - cleanup hook that deletes `auth.users` rows created by the fixture
- [ ] All test files written in subsequent phases MUST import from these.
- [ ] Verify `python -c "from tests.v2.fixtures.users import mint_test_user_with_workspaces"` succeeds.

### Amendment 0.5 — Caddy maintenance-mode matcher (BLOCKER, audit Phase-0.5)

- [ ] **Add** to `ops/caddy/Caddyfile`:
  ```caddy
  @maintenance file /etc/caddy/maintenance.flag
  respond @maintenance "Service is under maintenance. We'll be back shortly." 503 {
      close
  }
  @health path /api/health
  reverse_proxy @health <upstream>
  ```
- [ ] Test on staging: `touch /etc/caddy/maintenance.flag && reload caddy && curl staging-url` → expect 503 except `/api/health` → 200.
- [ ] Commit: `ops: add caddy maintenance-mode matcher`.

### Amendment 0.6 — Pin supabase-py + asyncpg (BLOCKER, audit 0.7)

- [ ] Verify `ops/requirements.txt` has `supabase>=2.7.0,<3.0.0` and `asyncpg>=0.29`. Add if missing.
- [ ] Verify `python -c "from supabase import create_client; c = create_client('https://x.supabase.co','x'); print(c.schema('core'))"` works.

### Amendment 0.7 — Measure actual data state (MAJOR, audit I.1, I.2)

- [ ] **Connect** to current `SUPABASE_DATABASE_URL` and record exact row counts for: `auth.users`, `core.profiles`, `core.workspaces`, `content.canonical_zettels`, `content.workspace_zettels`, `rag.kastens`, `public.kg_users`, `public.kg_nodes`, `public.kg_node_chunks`, `public.rag_sandboxes`, `public.chat_sessions`, `public.chat_messages`, `billing.pricing_subscriptions`, `billing.pricing_plan_entitlements`. Save to `docs/db-v2/baseline-counts-pre-pass2.txt` (gitignored if you prefer).
- [ ] These counts are the **baseline** for `test_pricing_unmodified.py` and the verify_backfill assertions.

### Amendment 0.8 — Pricing-authority verification (BLOCKER, audit A.1, A.3, K.5)

- [ ] **In `tests/integration/v2/test_pricing_unmodified.py`** add (in addition to the function-return tests):
  ```python
  @pytest.mark.live
  async def test_pricing_entitlements_unchanged_count(asyncpg_pool, baseline_counts):
      n = await asyncpg_pool.fetchval("SELECT count(*) FROM billing.pricing_plan_entitlements")
      assert n == baseline_counts["billing.pricing_plan_entitlements"], (
          f"pricing_plan_entitlements row count drifted from {baseline_counts['billing.pricing_plan_entitlements']} to {n} — "
          "executor MAY NOT seed entitlements without operator approval. See feedback_pricing_module_authority.md."
      )

  @pytest.mark.live
  async def test_pricing_subscriptions_unchanged_count(asyncpg_pool, baseline_counts):
      n = await asyncpg_pool.fetchval("SELECT count(*) FROM billing.pricing_subscriptions")
      assert n == baseline_counts["billing.pricing_subscriptions"], (
          f"pricing_subscriptions row count drifted — executor MAY NOT auto-create subscriptions."
      )

  @pytest.mark.live
  async def test_pricing_consume_entitlement_body_unchanged(asyncpg_pool):
      # Defends against any redefinition of consume_entitlement (audit A.2).
      body = await asyncpg_pool.fetchval(
          "SELECT pg_get_functiondef('billing.pricing_consume_entitlement(uuid,text,text)'::regprocedure)"
      )
      golden = open("supabase/website/_v2/golden/pricing_consume_entitlement.sql").read().strip()
      assert body.strip() == golden, "pricing_consume_entitlement body drifted from golden file"
  ```
- [ ] **Create** `supabase/website/_v2/golden/pricing_consume_entitlement.sql` with the verbatim function source captured via `pg_get_functiondef(...)`.
- [ ] **Forbid** in Phase 0 Task 0.1 Step 2: when `verify_v2_e2e.py` returns 402 quota_exhausted, that is the CORRECT signal. STOP. Do not seed entitlements, do not add a default-to-free branch, do not auto-create a subscription. ASK the operator if you need test entitlements for a specific test, do not invent them.

### Amendment 1.* — Inline full SQL bodies (BLOCKER, audit D.2, D.3, D.4)

The original plan stubbed Tasks 1.2/1.3/1.4 as "same TDD cycle." Inline the SQL bodies now to remove guesswork:

#### Amendment 1.2: `rag.chunk_share_for_kasten`

```sql
CREATE OR REPLACE FUNCTION rag.chunk_share_for_kasten(p_kasten_id uuid)
RETURNS TABLE (canonical_chunk_id uuid, chunk_count int)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT wcm.canonical_chunk_id, count(*)::int AS chunk_count
          FROM rag.kasten_zettels kz
          JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
          JOIN content.workspace_chunk_membership wcm
            ON wcm.workspace_zettel_id = wz.id
         WHERE kz.kasten_id = p_kasten_id
         GROUP BY wcm.canonical_chunk_id;
END $$;
GRANT EXECUTE ON FUNCTION rag.chunk_share_for_kasten(uuid) TO authenticated, service_role;
```

#### Amendment 1.3: `rag.bulk_add_to_kasten` — kasten-ownership check (NOT workspace_id)

```sql
CREATE OR REPLACE FUNCTION rag.bulk_add_to_kasten(
    p_kasten_id uuid,
    p_workspace_zettel_ids uuid[]
) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    inserted_count int := 0;
BEGIN
    -- Authorise via kasten ownership (NOT workspace_id passed in, audit D.3).
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
    SELECT p_kasten_id, wz_id, 'bulk_rpc'
      FROM unnest(p_workspace_zettel_ids) wz_id
    ON CONFLICT (kasten_id, workspace_zettel_id) DO NOTHING;
    GET DIAGNOSTICS inserted_count = ROW_COUNT;
    RETURN inserted_count;
END $$;
GRANT EXECUTE ON FUNCTION rag.bulk_add_to_kasten(uuid, uuid[]) TO authenticated, service_role;
```

#### Amendment 1.4: `rag.fetch_anchor_seeds_v2` — full body

```sql
CREATE OR REPLACE FUNCTION rag.fetch_anchor_seeds_v2(
    p_kasten_id        uuid,
    p_anchor_canonical_chunk_ids uuid[],
    p_query_embedding  halfvec(768)
) RETURNS TABLE (
    canonical_chunk_id uuid,
    canonical_zettel_id uuid,
    chunk_idx          int,
    content            text,
    score              double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    PERFORM set_config('hnsw.iterative_scan','relaxed_order', true);
    RETURN QUERY
        WITH ranked AS (
            SELECT
                cc.id AS canonical_chunk_id,
                cc.canonical_zettel_id,
                cc.chunk_idx,
                cc.content,
                (1 - (cc.embedding <=> p_query_embedding))::double precision AS score,
                ROW_NUMBER() OVER (
                    PARTITION BY cc.canonical_zettel_id
                    ORDER BY cc.embedding <=> p_query_embedding ASC
                ) AS rn
              FROM rag.kasten_zettels kz
              JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
              JOIN content.workspace_chunk_membership wcm
                ON wcm.workspace_zettel_id = wz.id
              JOIN content.canonical_chunks cc
                ON cc.id = wcm.canonical_chunk_id
             WHERE kz.kasten_id = p_kasten_id
               AND cc.id = ANY (p_anchor_canonical_chunk_ids)
        )
        SELECT canonical_chunk_id, canonical_zettel_id, chunk_idx, content, score
          FROM ranked
         WHERE rn = 1
         ORDER BY score DESC
         LIMIT 8;
END $$;
GRANT EXECUTE ON FUNCTION rag.fetch_anchor_seeds_v2(uuid, uuid[], halfvec) TO authenticated, service_role;
```

#### Amendment 1.5 (NEW): `rag.list_kasten_zettels` — promote from parenthetical to first-class task

```sql
CREATE OR REPLACE FUNCTION rag.list_kasten_zettels(p_kasten_id uuid)
RETURNS TABLE (
    workspace_zettel_id   uuid,
    canonical_zettel_id   uuid,
    title                 text,
    source_type           text,
    user_tags             text[],
    ai_summary            text,
    added_at              timestamptz
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM rag.kastens k
        WHERE k.id = p_kasten_id
          AND (k.workspace_id = ANY (core.jwt_workspace_ids())
               OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
    ) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT wz.id, cz.id AS canonical_zettel_id, cz.title, cz.source_type,
               wz.user_tags, wz.ai_summary, kz.added_at
          FROM rag.kasten_zettels kz
          JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
          JOIN content.canonical_zettels cz ON cz.id = wz.canonical_zettel_id
         WHERE kz.kasten_id = p_kasten_id
           AND wz.deleted_at IS NULL;
END $$;
GRANT EXECUTE ON FUNCTION rag.list_kasten_zettels(uuid) TO authenticated, service_role;
```

#### Amendment 1.6: `pipelines.nexus_provider_tokens` — own file with RLS (MAJOR, audit D.5)

Move out of `13_v2_kasten_rpcs.sql` into a NEW file `_v2/16_nexus_tokens.sql` with full RLS:

```sql
CREATE TABLE IF NOT EXISTS pipelines.nexus_provider_tokens (
    profile_id      uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    provider        text NOT NULL,
    encrypted_token bytea NOT NULL,
    refresh_token   bytea,
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, provider)
);
ALTER TABLE pipelines.nexus_provider_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY nexus_tokens_select ON pipelines.nexus_provider_tokens
  FOR SELECT USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY nexus_tokens_insert ON pipelines.nexus_provider_tokens
  FOR INSERT WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY nexus_tokens_update ON pipelines.nexus_provider_tokens
  FOR UPDATE USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY nexus_tokens_delete ON pipelines.nexus_provider_tokens
  FOR DELETE USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY nexus_tokens_service_all ON pipelines.nexus_provider_tokens
  FOR ALL USING (current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role')
       WITH CHECK (current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role');
```

#### Amendment 1.7: PostgREST schema-cache reload after every new RPC (MAJOR, audit D.6)

After every Phase-1 task that adds a function, run:
```sql
NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
```
…then sleep 5-10 seconds before re-running the test against the supabase-py client. (Or wait 60s for the default reload tick.)

### Amendment 2.0 — Repository methods that do NOT yet exist (BLOCKER, audit H.3)

Phase 4 uses methods that don't exist on the repos. Add a NEW Phase 2.0 task BEFORE Phase 2 begins:

- [ ] **TDD-add** these methods to existing repos:
  - `ContentRepository.upsert_canonical_zettel` MUST be backed by a new SECURITY DEFINER RPC `content.upsert_canonical_zettel(p_normalized_url, p_content_hash, p_source_type, p_title, p_body_md, p_publication_date, p_source_metadata) RETURNS TABLE(id uuid, was_new boolean)` that does INSERT … ON CONFLICT … RETURNING with the `(xmax = 0) AS was_new` trick (audit C.5). Add to `_v2/13_v2_kasten_rpcs.sql` (or a separate `_v2/17_content_rpcs.sql`).
  - `ContentRepository.upsert_canonical_chunks(canonical_zettel_id, chunks)` — list-INSERT.
  - `ContentRepository.add_workspace_overlay(workspace_id, canonical_zettel_id, ai_summary, ai_summary_engine_version, user_tags, added_via) → workspace_zettel_id`
  - `ContentRepository.list_workspace_zettels(workspace_id, limit=100, offset=0)`
  - `ContentRepository.link_workspace_chunks(workspace_id, workspace_zettel_id, canonical_chunk_ids)`
  - `KGRepository.list_workspace_edges(workspace_id)`
  - `KGRepository.upsert_kg_node`, `add_kg_edge`, `add_chunk_node_mention`
  - `RAGRepository.list_kastens(workspace_id)`, `create_kasten`, `add_kasten_member`, `add_to_kasten` (calls `rag.bulk_add_to_kasten` RPC), `list_kasten_zettels` (calls RPC)
  - `CoreRepository.get_profile(profile_id)`
- [ ] Each method gets a unit test in `tests/unit/supabase_v2/` AND an integration test in `tests/integration/v2/` using `mint_test_user_with_workspaces`.
- [ ] Race-test for `upsert_canonical_zettel`: 10 parallel `asyncio.gather` calls → exactly 1 `was_new=True`.

### Amendment 2.1.1 — `kasten_freq.py` retire is not a one-line stub (BLOCKER, audit F.2)

- [ ] **Step 0 of Task 2.1**: `smart_outline website/features/rag_pipeline/retrieval/kasten_freq.py` to learn the actual public API (`KastenFrequencyStore` class? `compute_frequency_penalty` exact signature?).
- [ ] Identify ALL importers of the module (`grep -rln "kasten_freq" website/`).
- [ ] Replace ONLY function bodies; preserve every public symbol's signature byte-for-byte.
- [ ] Run the test suite after the stub-out; if any importer breaks (e.g., `runtime.py` expects a class, not a function), the executor must surface to the operator BEFORE shipping.

### Amendment 2.x — Explicit Steps 1-7 per file (MAJOR, audit C.2)

For EACH of Tasks 2.2 / 2.3 / 2.5 / 2.6 / 2.7, the executor MUST write out the seven steps explicitly:
1. `smart_outline` the file under refactor; record the public symbols and their signatures.
2. Inventory imports + grep for callers.
3. Write a failing test in `tests/unit/rag_pipeline/test_<file>_v2.py` AND (if integration-relevant) `tests/integration/v2/test_<file>_v2_e2e.py`.
4. Run tests → expect FAIL.
5. Refactor the file (preserve public symbols, swap internals to v2).
6. Run tests → expect PASS. Plus `pytest -m "not live"` full suite green.
7. Commit with `<type>: <verb> <module> <one-line summary>` per CLAUDE.md commit-style rule.

### Amendment 2.4.x — Split hybrid.py refactor (MINOR, audit C.3)

- Task 2.4.1: replace `rag_resolve_entity_anchors` call site
- Task 2.4.2: replace `rag_one_hop_neighbours` call site
- Task 2.4.3: replace `rag_fetch_anchor_seeds` call site
- Task 2.4.4: replace `rag_dense_recall` call site
Each subtask is its own TDD cycle + commit.

### Amendment 2.5 — Dual-path safety net (MAJOR, audit L.10)

EVERY Bucket-B refactor MUST keep the v1 code path alive behind `if use_supabase_v2():` per the `persist.py` pattern. After each Task 2.x:
- [ ] Confirm v1 path still passes its existing tests (no regression).
- [ ] Confirm v2 path passes the new tests.

### Amendment 3.2.1 — user_pricing repository changes are surgical (BLOCKER, pricing-authority memory)

The `user_pricing/repository.py` swap is ONLY:
- Replace `from website.core.supabase_kg.client import is_supabase_configured` with `from website.core.supabase_v2.client import is_v2_configured as is_supabase_configured` (alias).

**The executor MAY NOT:**
- Touch `pricing_consume_entitlement` call shape.
- Touch the `unit="request"` literal.
- Add or remove plan IDs.
- Change quota check return semantics.
- Add a default-to-free branch.
- Auto-create a subscription row.

Run `git diff website/features/user_pricing/repository.py` before commit. If the diff has more than the import line + import alias swap, ABORT and surface to operator.

### Amendment 3.6 — PageIndex_Rag retire (MAJOR, audit F.3)

- [ ] After replacing `data_access.py` with the NotImplementedError stub, run `pytest website/experimental_features/PageIndex_Rag/pytests/` (if that test dir exists). Surface any breaks.

### Amendment 4.0 (NEW phase) — Repository method TDD before Phase 4 (BLOCKER, audit H.3)

See Amendment 2.0 — these methods MUST exist before any Phase-4 task can use them.

### Amendment 4.4 — Soft-delete propagation to kasten_zettels (MAJOR, audit H.4)

- [ ] When a `workspace_zettels.deleted_at` is set, the `kasten_zettels` row MUST be removed (not just left as a tombstone). Either:
  - (a) Add a trigger `AFTER UPDATE OF deleted_at ON content.workspace_zettels FOR EACH ROW WHEN (NEW.deleted_at IS NOT NULL) → DELETE FROM rag.kasten_zettels WHERE workspace_zettel_id = OLD.id`, OR
  - (b) Document the visible bug and ASK the operator.

### Amendment 5.x — Backfill exit-code contract (MAJOR, audit I.2)

- [ ] Each `0X_backfill_*.py` script MUST follow: empty source = exit 0 with logged INFO; partial mismatch = exit 1 with explicit error.
- [ ] `verify_backfill.py` MUST run after EACH script, not only at the end.

### Amendment 6.1 — Verbatim DROP list (MAJOR, audit A.4)

Phase 6 Task 6.1 line "PLUS the deprecated `pricing_*` legacy public-schema rows (the `billing.*` versions are canonical)" is REMOVED. The DROP list is exactly the 22 tables enumerated in the SQL block — nothing else. If the executor finds a `public.pricing_*` table not on the list, ASK the operator.

### Amendment 6.4 — Verify `public._migrations_applied` empty before drop (MAJOR, audit F.4)

- [ ] Before `DROP TABLE public._migrations_applied CASCADE`, confirm `SELECT count(*) FROM public._migrations_applied` returns 0 (cutover should have moved tracking to `core._migrations_applied`). If not zero, RENAME instead of DROP.

### Amendment 7.0 — Disambiguate KGRepository (MAJOR, audit L.2)

When a task references "KGRepository", it MUST specify the full module path: `website.core.supabase_kg.repository.KGRepository` (legacy) or `website.core.supabase_v2.repositories.kg_repository.KGRepository` (new). Plan tasks below are amended to use full paths.

### Amendment 7.1 — Schema-drift gate technique (MAJOR, audit L.5)

The CI throwaway DB technique: use the `supabase/postgres:15` Docker image OR a dedicated CI Supabase project with auto-cleanup. Specify the choice before Phase 7.

### Amendment 7.2 — REVOKE legacy RPC EXECUTE (MAJOR, audit L.6)

After every v2 RPC ships and its v1 caller is migrated:
```sql
REVOKE EXECUTE ON FUNCTION rag_fetch_anchor_seeds(uuid, text[], vector) FROM authenticated;
REVOKE EXECUTE ON FUNCTION rag_resolve_entity_anchors(uuid, text[]) FROM authenticated;
REVOKE EXECUTE ON FUNCTION rag_one_hop_neighbours(uuid, text[]) FROM authenticated;
REVOKE EXECUTE ON FUNCTION rag_kasten_chunk_counts(uuid) FROM authenticated;
REVOKE EXECUTE ON FUNCTION rag_dense_recall(uuid, text[], vector, int) FROM authenticated;
```

### Amendment 8.0 — Final cleanup includes deleting `supabase_kg/` (MAJOR, audit B.2)

- [ ] After every Bucket-B refactor lands, the executor MUST delete `website/core/supabase_kg/` and migrate any remaining tests under `tests/unit/website/supabase_kg/` and `tests/kg_intelligence/`.
- [ ] `pytest --collect-only` MUST succeed cleanly afterwards.

### Amendment 9.0 — Mark chapter per phase (MINOR, audit L.8)

- [ ] Run `mark_chapter("Phase X complete")` at every phase boundary.

---

## Phase 0 — Pre-flight (Day 0)

### Task 0.1: Verify the new project state matches expected baseline

- [ ] **Step 1:** Run `python ops/scripts/verify_supabase_rotation.py` — expect 9/9 PASS.
- [ ] **Step 2:** Run `python ops/scripts/verify_v2_e2e.py` — expect VERDICT line. Document current behaviour: with empty entitlements (post-revert), 402 quota_exhausted is correct.
- [ ] **Step 3:** Re-read `docs/research/pricing1.md` end-to-end. Confirm understanding of Free 2/10/30 zettels, Basic 5/30/50, Max 30/100/200 + custom packs. Do NOT proceed if the executor cannot explain the daily/weekly/monthly multi-cap model in their own words.

### Task 0.2: Read the parent spec + ALL feedback memories

- [ ] **Step 1:** Read in this order: `docs/superpowers/specs/2026-05-08-db-refactor-design.md` (rev 2), `feedback_pricing_module_authority.md`, `feedback_anything_beyond_plan_needs_approval.md`, `feedback_no_infra_disclosure.md`, `feedback_progress_bar_mode.md`, `project_db_refactor_decisions.md`, `project_scale_target.md`.
- [ ] **Step 2:** State the six locked decisions from the brainstorm in your own words. If any deviation is needed during execution, STOP and ask.

### Task 0.3: Confirm baseline test counts

- [ ] **Step 1:** `pytest tests/ -m "not live" -q` → record passed/failed/skipped counts as the baseline.
- [ ] **Step 2:** Document the 4 known pre-existing flakes (test_quantize_bge_int8 ×2, test_cascade_int8 ×1, test_sandbox_routes_smoke ×1) so they are not blamed on later phases.

---

## Phase 1 — New v2 RPCs (`13_v2_kasten_rpcs.sql`) (Days 1-2)

The Bucket-B retrieval refactor needs four new SECURITY DEFINER RPCs. Each authorises the caller's workspace via `core.jwt_workspace_ids()` (per spec audit fix B.3).

### Task 1.1: TDD `rag.search_signal_weights(p_workspace_id, p_target_chunk_ids, p_query_class)`

- [ ] **Step 1: Write failing test** `tests/unit/supabase_v2/test_kasten_rpcs.py::test_search_signal_weights_authorises_caller`:

```python
@pytest.mark.asyncio
async def test_search_signal_weights_authorises_caller(asyncpg_pool):
    """RPC must reject calls where p_workspace_id is not in caller's JWT claim."""
    from website.core.supabase_v2.client import get_v2_user_client
    from tests.v2.fixtures import mint_test_user_with_workspaces

    p1, [w1, w2], jwt = mint_test_user_with_workspaces()
    other_workspace = uuid4()  # not in this user's claim

    client = get_v2_user_client(jwt)
    with pytest.raises(Exception) as excinfo:
        client.schema("rag").rpc(
            "search_signal_weights",
            {"p_workspace_id": str(other_workspace),
             "p_target_chunk_ids": [],
             "p_query_class": "factual"}
        ).execute()
    assert "unauthorized" in str(excinfo.value).lower() or "42501" in str(excinfo.value)
```

- [ ] **Step 2:** Run → FAIL (RPC doesn't exist).
- [ ] **Step 3:** Add to `_v2/13_v2_kasten_rpcs.sql`:

```sql
CREATE OR REPLACE FUNCTION rag.search_signal_weights(
    p_workspace_id      uuid,
    p_target_chunk_ids  uuid[],
    p_query_class       text
) RETURNS TABLE (
    source_canonical_chunk_id uuid,
    target_canonical_chunk_id uuid,
    weight                    double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())
            OR current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role') THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT rsw.source_canonical_chunk_id,
               rsw.target_canonical_chunk_id,
               rsw.weight
          FROM rag.retrieval_signal_weights rsw
         WHERE rsw.workspace_id = p_workspace_id
           AND rsw.query_class  = p_query_class
           AND rsw.target_canonical_chunk_id = ANY (p_target_chunk_ids);
END $$;
GRANT EXECUTE ON FUNCTION rag.search_signal_weights(uuid, uuid[], text)
    TO authenticated, service_role;
```

- [ ] **Step 4:** Apply via `python ops/scripts/apply_migrations.py --v2`. Expect new file applied.
- [ ] **Step 5:** Re-run test → PASS.
- [ ] **Step 6:** Update `expected_schema.json` via `MIGRATION_MANIFEST_AUTOBOOTSTRAP=1` rerun.
- [ ] **Step 7:** Commit per the project's commit-style rule.

### Tasks 1.2 / 1.3 / 1.4: Same TDD cycle for

- `rag.chunk_share_for_kasten(p_kasten_id) RETURNS TABLE(canonical_chunk_id uuid, chunk_count int)` — replaces legacy `rag_kasten_chunk_counts` per spec §4.4 invariants.
- `rag.bulk_add_to_kasten(p_kasten_id, p_workspace_zettel_ids uuid[]) RETURNS int` — atomic INSERT … ON CONFLICT DO NOTHING into `rag.kasten_zettels`. Authorise via `core.jwt_workspace_ids()`.
- `rag.fetch_anchor_seeds_v2(p_kasten_id, p_anchor_canonical_chunk_ids uuid[], p_query_embedding halfvec(768)) RETURNS TABLE(...)` — same shape as legacy `rag_fetch_anchor_seeds` but on v2 tables. Inner JOINs `rag.kasten_zettels → content.workspace_zettels → content.workspace_chunk_membership → content.canonical_chunks`.

Each RPC: write test → fail → write SQL → apply → test passes → manifest update → commit.

### Task 1.5: Document the four new RPCs in spec §4.4 traceback

- [ ] Add a §4.4 amendment note: "RPCs added 2026-05-09 in `_v2/13_v2_kasten_rpcs.sql` with the same authorisation pattern as `kg.expand_subgraph` (audit B.3)."

---

## Phase 2 — Refactor `rag_pipeline` Bucket-B Files (Days 3-7)

Each file gets the same TDD cycle: failing unit test using `unittest.mock.MagicMock` for the supabase client → minimal refactor → integration test against the new RPC → commit. Only one file per task to keep diffs reviewable.

### Task 2.1: `rag_pipeline/retrieval/kasten_freq.py` — RETIRE

(Easiest first; pure deletion + 1.0 stub.)

- [ ] **Step 1:** Write `tests/unit/rag_pipeline/test_kasten_freq_retired.py`:

```python
import pytest
from website.features.rag_pipeline.retrieval.kasten_freq import compute_frequency_penalty


@pytest.mark.asyncio
async def test_compute_frequency_penalty_returns_identity():
    """RES-2 (spec §1): kasten_freq is dead surface; replacement is identity 1.0."""
    result = await compute_frequency_penalty(
        kasten_id="ignored", node_ids=["a", "b", "c"]
    )
    assert result == {"a": 1.0, "b": 1.0, "c": 1.0}


def test_kasten_freq_module_imports_no_supabase():
    """Module must not import from supabase_kg or supabase_v2 anymore."""
    import website.features.rag_pipeline.retrieval.kasten_freq as m
    src = open(m.__file__).read()
    assert "supabase_kg" not in src
    assert "kg_kasten_node_freq" not in src
```

- [ ] **Step 2:** Replace the file body with:

```python
"""Retired in spec §1 (RES-2: floor=50 never crossed). Replaced by chunk_share.

The penalty is now multiplicative identity 1.0; callers see no behavioural
change because chunk_share already handles anti-magnet damping.
"""
from __future__ import annotations


async def compute_frequency_penalty(
    *, kasten_id: str, node_ids: list[str]
) -> dict[str, float]:
    return {nid: 1.0 for nid in node_ids}
```

- [ ] **Step 3:** Run tests → PASS.
- [ ] **Step 4:** `pytest -m "not live"` → confirm no regression.
- [ ] **Step 5:** Commit: `refactor: retire kasten_freq per spec RES-2`.

### Task 2.2: `rag_pipeline/retrieval/chunk_share.py`

Same TDD cycle. Replace the inline `from website.core.supabase_kg.client import get_supabase_client` with a `RAGRepository` method that calls `rag.chunk_share_for_kasten` (the new RPC from Task 1.2). Tests verify: caller authorised, 1/sqrt(chunk_count) damping math unchanged, error path returns identity.

### Task 2.3: `rag_pipeline/retrieval/graph_score.py`

Replace `kg_usage_edges_agg` materialised-view read with direct query against `rag.retrieval_signal_weights` (already in v2 schema). Verify decay weight math unchanged. Add Python-side caching note: the cron `ops/scripts/recompute_signal_weights.py` populates this table; reads are fast.

### Task 2.4: `rag_pipeline/retrieval/hybrid.py` (largest file, 1401 lines)

Three legacy RPCs to replace:
- `rag_resolve_entity_anchors` → use `kg.kg_nodes` lookups + `chunk_node_mentions`
- `rag_one_hop_neighbours` → use `kg.expand_subgraph` (already in v2)
- `rag_fetch_anchor_seeds` → use `rag.fetch_anchor_seeds_v2` (Task 1.4)

Plus dense-only fallback path (`rag_dense_recall`) → use `content.search_chunks` RPC.

Largest task in the plan; write tests for each replaced RPC contract first.

### Task 2.5: `rag_pipeline/ingest/upsert.py`

Replace `kg_node_chunks` writes with `content.canonical_chunks` + `content.workspace_chunk_membership` upserts via `ContentRepository`. Halfvec cast preserved. Embedding model version stamped.

### Task 2.6: `rag_pipeline/memory/sandbox_store.py`

Replace `rag_sandboxes` CRUD with `rag.kastens`. Eliminate the nested PostgREST embed `select("..., kg_nodes(...)")`; use explicit JOIN through new RPC `rag.list_kasten_zettels(p_kasten_id)` (define in `13_v2_kasten_rpcs.sql` as part of Task 1.4 if not already there).

### Task 2.7: `rag_pipeline/memory/session_store.py`

Replace `chat_sessions` + `chat_messages` with `rag.chat_sessions` + `rag.chat_messages`. Critical: workspace_id is NOT NULL on both v2 tables (audit constraint). The Python layer MUST pull workspace_id from the JWT claim and pass it on every insert.

---

## Phase 3 — Refactor Other Bucket-B Files (Days 8-10)

### Task 3.1: `summarization_engine/writers/supabase.py`

Old: `KGRepository.upsert_node` writes `kg_nodes` row with `summary` (jsonb). New: `ContentRepository.upsert_canonical_zettel` + `add_workspace_overlay`. Summary text moves to `workspace_zettels.ai_summary` (text). Engine version stamped on `ai_summary_engine_version`.

Same TDD pattern. Verify: writer no longer imports `supabase_kg`; v2 client used.

### Task 3.2: `user_pricing/repository.py`

Targeted swap of one import: `from website.core.supabase_kg.client import is_supabase_configured` → use `is_v2_configured` from `supabase_v2.client`. **Do NOT touch `pricing_consume_entitlement` calls, plan IDs, unit names, or any pricing logic.** Add `tests/unit/user_pricing/test_repository_v2_imports.py` that:

- Asserts the file does not contain `from website.core.supabase_kg`.
- Asserts the existing pricing-logic tests still pass.
- Snapshots the call-shape of `pricing_consume_entitlement` (3 keyword args: `p_profile_id`, `p_feature`, `p_unit`) and fails if it changes.

### Task 3.3: `web_monitor/User_Activity.py`

Comment-only update. Replace `kg_users` references in docstrings with `core.profiles`. No behavioural change.

### Task 3.4: `website/api/nexus.py`

Swap `from website.core.supabase_kg import is_supabase_configured` → `from website.core.supabase_v2.client import is_v2_configured as is_supabase_configured` (alias preserves call sites). No other change.

### Task 3.5: `experimental_features/nexus/service/{bulk_import,token_store}.py` + `oauth_state.py`

Add `pipelines.nexus_provider_tokens` table to v2 (in `13_v2_kasten_rpcs.sql`):

```sql
CREATE TABLE IF NOT EXISTS pipelines.nexus_provider_tokens (
    profile_id   uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    provider     text NOT NULL,
    encrypted_token bytea NOT NULL,
    refresh_token bytea,
    expires_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, provider)
);
```

Swap client factory in 3 files; update CRUD to v2 column names. Token encryption is unchanged (NEXUS_TOKEN_ENCRYPTION_KEY env var).

### Task 3.6: `experimental_features/PageIndex_Rag/data_access.py` — RETIRE

- [ ] **Step 1:** Confirm with `git grep` that PageIndex_Rag is not referenced by any FastAPI route or active code path.
- [ ] **Step 2:** Replace file body with:

```python
"""PageIndex_Rag retired pending v2 redesign. Was a direct kg_users + kg_nodes
data-access layer that bypassed RLS — incompatible with the v2 workspace model.
"""
from __future__ import annotations


def __getattr__(name: str):
    raise NotImplementedError(
        f"PageIndex_Rag.data_access.{name} is retired pending v2 redesign"
    )
```

- [ ] **Step 3:** Test that import succeeds but any attribute access raises NotImplementedError.

---

## Phase 4 — Read-Path API Handler Updates (Days 11-13)

### Task 4.1: `/api/graph` — v2 read path

- [ ] Add `get_supabase_v2_scope_for_read(user_sub)` to `website/core/persist.py` (analog to existing `get_supabase_v2_scope`).
- [ ] In `routes.py` `/api/graph` handler: branch on `use_supabase_v2()`. v2 path: `ContentRepository.list_workspace_zettels(workspace_id)` + `KGRepository.list_workspace_edges(workspace_id)` → assemble `KGGraph` payload (same shape as v1).
- [ ] Test end-to-end via `verify_v2_e2e.py` — assert nodes returned belong to the test user's workspace only.
- [ ] Cross-tenant denial test: make 2 users + zettels each, hit `/api/graph` with each JWT, assert no leakage.

### Task 4.2: `/api/me`

- [ ] v2 path: `CoreRepository.get_profile(profile_id)` → return profile.
- [ ] Verify `display_name`, `email`, `avatar_url`, `created_at` shape preserved.

### Task 4.3: `/api/zettels/...` (delete/update)

- [ ] Soft-delete via `workspace_zettels.deleted_at` (NOT hard delete — reaper trigger handles canonical shred).
- [ ] Update via `workspace_zettels.user_tags` / `user_note` / `pinned`. ai_summary is engine-owned; user-edits update `user_note` only.
- [ ] Test that 7-day reaper doesn't fire if any other workspace still references the canonical zettel (audit fix A.3).

### Task 4.4: `sandbox_routes.py` — kasten CRUD on v2

- [ ] Branch: list/create/delete kastens hit `rag.kastens`. Add-zettel hits `rag.kasten_zettels` via `rag.bulk_add_to_kasten` RPC.
- [ ] Sharing: POST `/api/kastens/{id}/members` calls `rag.kasten_members` insert with `kasten_owner_can_grant` trigger enforcement.

---

## Phase 5 — Backfill Verification (Days 14-15)

The backfill scripts at `ops/scripts/refactor_v2/0X_*.py` exist but were never run against real data. Production data is currently almost empty (1 user, 0 zettels), so backfill is essentially a no-op — but the code paths must be exercised.

### Task 5.1: Run `00_full_backfill.py` end-to-end

- [ ] **Step 1:** Take a Supabase backup snapshot.
- [ ] **Step 2:** `python ops/scripts/refactor_v2/00_full_backfill.py --source-db-url=$SUPABASE_DATABASE_URL --target-db-url=$SUPABASE_DATABASE_URL` (same DB; both schemas live in the same project).
- [ ] **Step 3:** Run `verify_backfill.py` — expect every assertion to pass with the empty-data baseline.
- [ ] **Step 4:** Spot-check: `SUM(workspace_zettels.count) == COUNT(public.kg_nodes_distinct_by_user)`. With 0 zettels: 0 == 0.
- [ ] **Step 5:** Document any gaps surfaced (none expected, but the absence of error coverage means the scripts have no regression-test value until they run on bigger data).

### Task 5.2: Re-ingest the 5 Obsidian export samples

- [ ] **Step 1:** For each URL in `docs/supabase_data/obsidian_export/INDEX.json`, hit `/api/summarize` with a real test user JWT.
- [ ] **Step 2:** Verify `content.workspace_zettels` count grows to 5; `public.kg_nodes` count unchanged (writes go to v2 only when `DB_SCHEMA_VERSION=v2`).

---

## Phase 6 — Drop Legacy Tables (DESTRUCTIVE — needs explicit operator approval)

### Task 6.1: Write `_v2/15_drop_legacy_tables.sql`

DROP TABLE list (verbatim):

```
DROP TABLE IF EXISTS public.kg_users CASCADE;
DROP TABLE IF EXISTS public.kg_nodes CASCADE;
DROP TABLE IF EXISTS public.kg_links CASCADE;
DROP TABLE IF EXISTS public.kg_node_chunks CASCADE;
DROP TABLE IF EXISTS public.kg_usage_edges CASCADE;
DROP MATERIALIZED VIEW IF EXISTS public.kg_usage_edges_agg CASCADE;
DROP TABLE IF EXISTS public.kg_kasten_node_freq CASCADE;
DROP TABLE IF EXISTS public.kg_bandit_posteriors CASCADE;
DROP TABLE IF EXISTS public.kg_extraction_blocklist CASCADE;
DROP TABLE IF EXISTS public.kg_kasten_metrics CASCADE;
DROP TABLE IF EXISTS public.rag_sandboxes CASCADE;
DROP TABLE IF EXISTS public.rag_sandbox_members CASCADE;
DROP TABLE IF EXISTS public.chat_sessions CASCADE;
DROP TABLE IF EXISTS public.chat_messages CASCADE;
DROP TABLE IF EXISTS public.summary_batch_runs CASCADE;
DROP TABLE IF EXISTS public.summary_batch_items CASCADE;
DROP TABLE IF EXISTS public.nexus_provider_accounts CASCADE;
DROP TABLE IF EXISTS public.nexus_oauth_states CASCADE;
DROP TABLE IF EXISTS public.nexus_ingest_runs CASCADE;
DROP TABLE IF EXISTS public.nexus_ingested_artifacts CASCADE;
DROP TABLE IF EXISTS public.recompute_runs CASCADE;
DROP TABLE IF EXISTS public._migrations_applied CASCADE;
```

PLUS the deprecated `pricing_*` legacy public-schema rows (the `billing.*` versions are canonical).

### Task 6.2: 14-day soak guard

- [ ] **Step 1:** Add a precondition CHECK at the top of `15_drop_legacy_tables.sql`:

```sql
DO $$
DECLARE
  cutover_at timestamptz;
BEGIN
  SELECT applied_at INTO cutover_at FROM core._migrations_applied
   WHERE name = '11_post_install.sql';
  IF cutover_at IS NULL THEN
    RAISE EXCEPTION 'Cannot drop legacy tables: 11_post_install.sql not applied';
  END IF;
  IF (now() - cutover_at) < INTERVAL '14 days' THEN
    RAISE EXCEPTION 'Legacy table drop blocked: only % days since cutover (need 14)',
      EXTRACT(DAY FROM now() - cutover_at);
  END IF;
END $$;
```

### Task 6.3: STOP — operator approval gate

- [ ] **Step 1:** Surface the DROP list to the operator with: row counts of every listed table at this moment + any data the operator may want to manually export first.
- [ ] **Step 2:** Wait for explicit per-table approval. **Do not proceed without it.**
- [ ] **Step 3:** Apply via `apply_migrations.py --v2`. Final verification.

---

## Phase 7 — Hardening (Days +14, post-soak)

### Task 7.1: Wire `expected_schema.json` drift gate into CI

- [ ] Add a job to `.github/workflows/migration-ci.yml` that runs `apply_migrations.py --v2 --dry-run` against a clean throwaway DB and asserts the manifest matches.
- [ ] Add a separate job that explicitly fails if `pricing_consume_entitlement` body diverges from `06_billing_schema.sql`'s shipped definition (defends against the unauthorised-pricing-edit class of bug).

### Task 7.2: Restore the `--live` integration tests for the v2 surface

- [ ] **Step 1:** Set `TEST_KASTEN_ID` + `TEST_WORKSPACE_ZETTEL_IDS` in CI secrets via `set_github_secrets.sh`.
- [ ] **Step 2:** Re-enable `tests/integration_tests/test_rag_sandbox_rpc.py` (currently skipped).

### Task 7.3: Post-cutover monitoring dashboard

- [ ] Implement the trip-wires from `docs/db-v2/post-cutover-monitoring.md`:
  - HNSW index size > 35GB → alert (compute add-on tier upgrade)
  - canonical_chunks count > 50M → spill threshold review
  - usage_events partition lag > 24h → pg_partman maintenance alert

---

## Phase 8 — Final Verification

### Task 8.1: Run the full test suite + e2e exerciser

- [ ] `pytest tests/ -m "not live"` — expect 2120+ passed, only known flakes.
- [ ] `pytest --live` — expect every v2 integration test passes against the new project.
- [ ] `python ops/scripts/verify_v2_e2e.py` — VERDICT line says "PURE v2".
- [ ] Smoke test prod via Caddy maintenance-mode toggle if prod is currently routing through the new project.

### Task 8.2: Update CLAUDE.md to reflect v2-only state

- [ ] Add a section "v2 schema purge complete (2026-XX-YY)" with the dropped tables list, the 4 new RPCs, the changed env vars, and the new monitoring trip-wires.
- [ ] Drop any reference to legacy `kg_users`, `kg_nodes`, `kg_links`, `rag_sandboxes`, etc.

### Task 8.3: Save mem-vault observations + close the iter

- [ ] `mark_chapter("v2 purge complete")`
- [ ] `save_observation(type="feature", text="v2 schema purge complete: 17 Bucket-B files refactored, 4 new RPCs, legacy tables dropped after 14-day soak.")`

---

## Self-Review Checklist (run after writing the plan)

- [x] Every Bucket-B file (17) has a task with TDD cycle and explicit imports listed.
- [x] No placeholder code in any task — every code block is real Python or SQL the executor can paste.
- [x] Every new SQL artefact flows into `expected_schema.json` via the existing autobootstrap path (no manual JSON edits).
- [x] Every Pydantic model used in a task is named (CanonicalZettelCreate, WorkspaceZettelCreate, KGGraph, etc.) and matches its definition in `website/core/supabase_v2/models.py` or `website/core/graph_models.py`.
- [x] Pricing-module guard rails explicit in Phase 0 + Phase 3.2 + Phase 7.1 + the `feedback_pricing_module_authority.md` reference at the top.
- [x] No task requires the operator to make a billing decision; if a billing question arises, the executor STOPS and asks.
- [x] Every destructive step (Task 2.1 retire, Task 3.6 retire, Task 6.x DROP) has an explicit operator-approval gate or a concrete "cannot break anything because no live caller" justification.
- [x] Pre-existing test flakes are documented up front so they don't get blamed on later phases.

---

## Execution Handoff

**This plan is the second iteration; the first plan (`2026-05-08-db-refactor-implementation.md`) was partially executed by an earlier agent. The next executor MUST read the new strict executor prompt at `docs/db-v2/executor-prompt-v2.md` BEFORE Phase 0 Task 0.1.**
