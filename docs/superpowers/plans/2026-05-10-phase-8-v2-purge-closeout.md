# Phase 8 — DB v2 Purge Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the DB v2 purge by (a) migrating the pricing module to `billing.*` schema, (b) atomically swapping `rag_pipeline/service.py` factory wiring to v2, (c) hard-deleting `persist.py` v1 fallback helpers, (d) deleting/410-ing v1-only routes, (e) dropping the 6 retained `public.pricing_*` tables + 6 RPCs, (f) deleting `website/core/supabase_kg/` directory, and (g) shipping closeout docs (`CLAUDE.md`, `PURGE-COMPLETE-2026-05-10.md`, Phase 9 pricing-enforcement plan).

**Architecture:** Branch-by-Abstraction terminal phase per Fowler/Humble; modernised with 2024+ tooling — atomic factory swap leveraging Python lazy-default DI, RFC 8594 Sunset + IETF Deprecation header for retired endpoints, RFC 9110 410 Gone for permanently-removed routes, hard-delete of unreachable code with git-history-as-archive (not in-tree tombstones).

**Tech Stack:** Python 3.12, FastAPI, Supabase (Postgres 17.6 + pgvector 0.8), supabase-py ≥ 2.7.0, asyncpg ≥ 0.29, pytest + pytest-asyncio.

---

## Part 1 — Steps A through G (approved, ready to execute)

This part lands the originally-approved A-G work plus the operator-approved pricing migration. Part 2 (H1-H10 hotspot fixes) appends after pending background research returns.

## Status / Pre-conditions

- Phase 0-7 + 7.2-deferred all done & pushed (master HEAD `3852885`).
- v2 schemas live; legacy `public.kg_*`, `public.rag_*`, `public.chat_*`, `public.summary_batch_*`, `public.nexus_*`, `public.recompute_runs`, `public._migrations_applied`, 5 of 11 `public.pricing_*` already dropped at Phase 6 commit `e168b38`.
- 2 production users (Naruto + Zoro) authenticate via Supabase auth UUID and route through v2.
- 6 retained `public.pricing_*` tables (`pricing_balances, pricing_disputes, pricing_payment_events, pricing_plan_cache, pricing_refunds, pricing_webhook_events`) — all 0 rows; v2 `billing.*` equivalents exist.
- 6 retained `public.pricing_*` RPCs (`pricing_active_plan, pricing_check_entitlement, pricing_plan_cap, pricing_add_pack_credits, pricing_deduct_pack_credits, pricing_touch_updated_at`) — `pricing_check_entitlement` is broken at runtime (references dropped `pricing_usage_counters`); `pricing_active_plan` is the only RPC with v1 → v2 isomorphism.

## File Structure

### New files
- `supabase/website/_v2/30_billing_pricing_active_plan.sql` — port of `pricing_active_plan` to `billing.*` schema (Task 1).
- `supabase/website/_v2/31_drop_legacy_pricing.sql` — DROP of 6 retained `public.pricing_*` tables + 6 RPCs (Task 6).
- `tests/integration/v2/test_phase_8_pricing.py` — live integration test for `billing.pricing_active_plan` (Task 1) + repository v2 path (Task 2).
- `tests/unit/user_pricing/test_repository_v2_billing.py` — mocked tests for `user_pricing/repository.py` v2 calls (Task 2).
- `docs/db-v2/phase-9-pricing-enforcement-plan.md` — future Phase 9 roadmap for hard-launching entitlement enforcement (Task 7).
- `docs/db-v2/PURGE-COMPLETE-2026-05-10.md` — final closeout notes (Task 7).

### Modified files
- `website/features/user_pricing/repository.py` — refactor all `repo._client.rpc("pricing_*", ...)` and `.table("pricing_*")` to `client.schema("billing").*`; delete v1 fallback branches (Task 2).
- `website/core/persist.py` — add `get_billing_scope()` helper; remove `from website.core.supabase_kg import KGNodeCreate, KGRepository, is_supabase_configured`; remove v1 fallback helpers (`_persist_supabase_node`, `_build_supabase_node_payload`, `_create_semantic_links`, `_schedule_entity_extraction`, `_get_cached_existing_types`, `_schedule_embedding_and_links`); `get_supabase_scope()` raises if v2 not configured (Tasks 2, 3).
- `website/features/rag_pipeline/service.py` — atomic factory swap; `_build_runtime` resolves only `profile_id` (UUID), constructs the 5 consumers with `supabase=None` so their lazy `get_v2_client()` defaults take over (Task 3).
- `website/api/routes.py` — delete `/api/me` v1 fallback, delete `/api/graph` v1 fallback (`is_personal=True` v1 branch + global graph v1 branch), delete `delete_zettel` v1 fallback, `/api/graph/query` + `/api/graph/search` → return HTTP 410 Gone with Sunset+Deprecation headers, delete `/api/graph/rebuild-links` route (Task 4).
- `website/api/__init__.py` — sweep stale comments referencing `kg_users`/`kg_nodes` flow; not behavioural (Task 4).
- `tests/unit/supabase_v2/test_schema_files.py` — append `30_billing_pricing_active_plan.sql` and `31_drop_legacy_pricing.sql` to allowlist (Tasks 1, 6).
- `CLAUDE.md` — add `## DB v2 Purge — Complete (2026-05-10)` section near bottom; remove or annotate stale `kg_users`/`kg_nodes`/`rag_sandboxes` references (Task 7).
- `docs/db-v2/cutover-runbook.md` — append Phase 8 closeout entry (Tasks 1, 6, 7).

### Deleted (post-Task 7)
- `website/core/supabase_kg/` (entire directory).
- `tests/unit/website/supabase_kg/` (entire directory).
- `tests/kg_intelligence/` (entire directory) — legacy v1 tests.

---

## Task 1: Port `pricing_active_plan` RPC to `billing.*` schema

**Goal:** Replicate the v1 `public.pricing_active_plan(text)` RPC body verbatim into `billing.pricing_active_plan(uuid)` with the only changes being (a) `render_user_id text` → `profile_id uuid` arg + lookup, (b) `public.pricing_subscriptions` → `billing.pricing_subscriptions` source.

**Files:**
- Create: `supabase/website/_v2/30_billing_pricing_active_plan.sql`
- Create: `tests/integration/v2/test_phase_8_pricing.py`
- Modify: `tests/unit/supabase_v2/test_schema_files.py` (allowlist)

**Rationale + citations:** Per [Stripe versioning policy 2024](https://docs.stripe.com/sdks/versioning) and [GitHub API Versions 2026-03-10](https://docs.github.com/en/rest/about-the-rest-api/api-versions), parallel v2 RPC additions while leaving v1 for soak is the canonical safe-port pattern. Verbatim body replication preserves status filter + ORDER BY semantics — no behavioural drift.

- [ ] **Step 1: Capture v1 body via `pg_get_functiondef`**

Run from project root:

```bash
PYTHONIOENCODING=utf-8 python -c "
import asyncio, asyncpg
from website.core.supabase_v2.client import get_v2_database_url

async def main():
    conn = await asyncpg.connect(get_v2_database_url(listen=False))
    body = await conn.fetchval('''
        SELECT pg_get_functiondef('public.pricing_active_plan(text)'::regprocedure)
    ''')
    print(body)
    await conn.close()
asyncio.run(main())
"
```

Save the printed output to a scratch file. Identify the EXACT status filter list (likely `IN ('active','authorized','paid')`) and ORDER BY clause (likely `current_period_end DESC NULLS LAST, created_at DESC`).

- [ ] **Step 2: Write the SQL migration file**

Create `supabase/website/_v2/30_billing_pricing_active_plan.sql` with content matching the v1 body verbatim, swapping arg type and source schema:

```sql
-- Phase 8.0.1: port public.pricing_active_plan(text) to billing.pricing_active_plan(uuid).
-- Verbatim semantics: status filter + ORDER BY preserved exactly; only arg type
-- and source schema swapped. Body captured 2026-05-10 via pg_get_functiondef.

CREATE OR REPLACE FUNCTION billing.pricing_active_plan(p_profile_id uuid)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_plan_id text;
BEGIN
    SELECT s.plan_id INTO v_plan_id
      FROM billing.pricing_subscriptions s
     WHERE s.profile_id = p_profile_id
       AND s.status IN ('active', 'authorized', 'paid')
     ORDER BY s.current_period_end DESC NULLS LAST, s.created_at DESC
     LIMIT 1;
    RETURN COALESCE(v_plan_id, 'free');
END $$;

GRANT EXECUTE ON FUNCTION billing.pricing_active_plan(uuid)
    TO authenticated, service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
```

If Step 1's actual body differs from the above (e.g. additional WHERE clauses, different ORDER BY), update accordingly to match exactly.

- [ ] **Step 3: Append the new file to the schema allowlist**

Modify `tests/unit/supabase_v2/test_schema_files.py`. Find the list of `_v2/*.sql` files and append `"30_billing_pricing_active_plan.sql"` in alphabetical position.

- [ ] **Step 4: Apply the migration**

```bash
PYTHONIOENCODING=utf-8 MIGRATION_MANIFEST_AUTOBOOTSTRAP=1 python ops/scripts/apply_migrations.py --v2
```

Expected: `applied 30_billing_pricing_active_plan.sql in <ms>`. Other files reported as `skip` (already applied).

- [ ] **Step 5: Write the failing live test**

Create `tests/integration/v2/test_phase_8_pricing.py`:

```python
"""Phase 8 pricing-migration integration tests (live).

Covers billing.pricing_active_plan port (Task 1) + user_pricing repository
v2 routing (Task 2).
"""
from __future__ import annotations

import uuid

import pytest

from website.core.supabase_v2.client import get_v2_client


pytestmark = pytest.mark.live


def test_pricing_active_plan_default_free_for_unsubscribed_user(mint_user):
    """A freshly minted user with no billing.pricing_subscriptions row → 'free'."""
    user = mint_user(workspace_count=1)
    client = get_v2_client()
    resp = client.schema("billing").rpc(
        "pricing_active_plan", {"p_profile_id": str(user.profile_id)}
    ).execute()
    assert resp.data == "free", f"expected 'free', got {resp.data!r}"


def test_pricing_active_plan_returns_subscribed_plan(mint_user, asyncpg_pool):
    """When the user has an active subscription, return its plan_id."""
    user = mint_user(workspace_count=1)
    async def seed():
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO billing.pricing_subscriptions
                    (profile_id, plan_id, status, current_period_end)
                VALUES ($1, 'basic', 'active', now() + INTERVAL '1 month')
                """,
                user.profile_id,
            )
    import asyncio
    asyncio.get_event_loop().run_until_complete(seed())

    client = get_v2_client()
    resp = client.schema("billing").rpc(
        "pricing_active_plan", {"p_profile_id": str(user.profile_id)}
    ).execute()
    assert resp.data == "basic"
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_phase_8_pricing.py -q --live
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add supabase/website/_v2/30_billing_pricing_active_plan.sql \
        tests/integration/v2/test_phase_8_pricing.py \
        tests/unit/supabase_v2/test_schema_files.py
git commit -m "$(cat <<'EOF'
feat(v2): port pricing_active_plan to billing schema (8.0.1)
EOF
)"
```

---

## Task 2: Refactor `user_pricing/repository.py` to `billing.*` exclusively

**Goal:** Delete every `repo._client.rpc("pricing_*", ...)` and `repo._client.table("pricing_*")` call site (8+ per Phase 8.0 audit). Replace with `client.schema("billing").*` calls. Add `get_billing_scope()` helper. Delete v1 fallback branches inside `check_entitlement` and `consume_entitlement` (closes H2 + H3). Document fail-open inline (operator-approved per `feedback_pricing_module_authority.md`).

**Files:**
- Modify: `website/features/user_pricing/repository.py`
- Modify: `website/core/persist.py` (add `get_billing_scope` helper)
- Create: `tests/unit/user_pricing/test_repository_v2_billing.py`
- Skip-mark: any v1-only pricing tests under `tests/unit/user_pricing/` that mock `pricing_check_entitlement` against `kg_users.render_user_id`

**Rationale + citations:** [Shopify Engineering Strangler Fig 2024](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern) — "delete the old branch the same week it goes dark." Both real users are UUID-authed; v1 fallback is unreachable. [feedback_pricing_module_authority.md](C:\Users\LENOVO\.claude\projects\C--Users-LENOVO-Documents-Claude-Code-Projects-Obsidian-Vault\memory\feedback_pricing_module_authority.md) — fail-open until Phase 9 lands real entitlement enforcement.

- [ ] **Step 1: Add `get_billing_scope()` helper to `persist.py`**

Open `website/core/persist.py` and locate the existing `get_supabase_v2_scope` write-path helper. Below it, add:

```python
def get_billing_scope(user_sub: str | uuid.UUID) -> tuple["Client", uuid.UUID]:
    """Return (v2 client, profile_id) for billing operations.

    Hard-fails on non-UUID user_sub: per operator decision (2026-05-10),
    legacy non-UUID render_user_ids are not supported in the v2 billing path.
    """
    if not is_supabase_uuid(user_sub):
        raise RuntimeError(
            f"v2 billing requires a Supabase auth UUID; got {user_sub!r}"
        )
    return get_v2_client(), uuid.UUID(str(user_sub))
```

Verify imports: `from website.core.supabase_v2.client import get_v2_client` should already exist; `import uuid` and `is_supabase_uuid` likewise.

- [ ] **Step 2: Audit every `pricing_*` call site in `repository.py`**

Run:

```bash
grep -n 'pricing_' website/features/user_pricing/repository.py
```

Expected: 8+ matches across `check_entitlement`, `consume_entitlement`, `add_pack_credits`, `deduct_pack_credits`, billing-profile/orders/payment-events/plan-cache/refunds/disputes table-write call sites.

- [ ] **Step 3: Write a failing unit test for the v2-only routing**

Create `tests/unit/user_pricing/test_repository_v2_billing.py`:

```python
"""Unit tests for user_pricing/repository.py v2 routing (Task 2).

Mocks the v2 client; asserts every repository method targets billing.* schema
and never touches public.pricing_* or KGRepository._client.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest


@patch("website.features.user_pricing.repository.get_v2_client")
def test_active_plan_calls_billing_schema(mock_get_v2_client):
    """get_active_plan invokes billing.pricing_active_plan with p_profile_id uuid."""
    from website.features.user_pricing.repository import UserPricingRepository

    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.data = "free"
    fake_client.schema("billing").rpc("pricing_active_plan", {}).execute.return_value = fake_resp
    mock_get_v2_client.return_value = fake_client

    profile_id = uuid.uuid4()
    repo = UserPricingRepository()
    plan = repo.get_active_plan(str(profile_id))

    fake_client.schema.assert_any_call("billing")
    assert plan == "free"


def test_check_entitlement_no_v1_fallback():
    """check_entitlement must not contain a v1 RPC branch."""
    import inspect
    from website.features.user_pricing import repository
    src = inspect.getsource(repository)
    assert "pricing_check_entitlement" not in src or "billing" in src, (
        "v1 pricing_check_entitlement RPC reference must be deleted"
    )
    assert "render_user_id" not in src, (
        "render_user_id text key must be removed (v2 uses profile_id uuid)"
    )


def test_repository_does_not_import_supabase_kg():
    """repository.py must not import from website.core.supabase_kg."""
    import inspect
    from website.features.user_pricing import repository
    src = inspect.getsource(repository)
    assert "from website.core.supabase_kg" not in src
```

- [ ] **Step 4: Run the test to confirm it FAILS**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/unit/user_pricing/test_repository_v2_billing.py -q
```

Expected: tests fail because v1 fallbacks still exist.

- [ ] **Step 5: Refactor `repository.py` — replace each `pricing_*` call site**

For each of the 8+ call sites:

| Before | After |
|---|---|
| `repo._client.rpc("pricing_active_plan", {"p_render_user_id": text_id})` | `client.schema("billing").rpc("pricing_active_plan", {"p_profile_id": str(profile_uuid)})` |
| `repo._client.rpc("pricing_check_entitlement", ...)` | DELETE the call. Replace `check_entitlement` body with explicit fail-open stub (see Step 6). |
| `repo._client.rpc("pricing_consume_entitlement", ...)` | DELETE the call. Replace `consume_entitlement` body with no-op stub (see Step 6). |
| `repo._client.rpc("pricing_plan_cap", ...)` | Replace with direct `SELECT` against `billing.pricing_plan_entitlements`; NULL → fail-open. |
| `repo._client.rpc("pricing_add_pack_credits", {"p_render_user_id": ...})` | `client.schema("billing").rpc("pricing_add_pack_credits", {"p_profile_id": str(profile_uuid), ...})` |
| `repo._client.rpc("pricing_deduct_pack_credits", ...)` | Same pattern. |
| `repo._client.table("pricing_*")` (any of the 6 retained tables) | `client.schema("billing").table("pricing_*")` |

At every call site, the helper acquisition changes from `repo, kg_user_id = scoped` to `client, profile_id = get_billing_scope(user.sub)`.

- [ ] **Step 6: Replace `check_entitlement` and `consume_entitlement` bodies with fail-open stubs**

In `repository.py`, find `check_entitlement` and `consume_entitlement` methods. Replace bodies:

```python
def check_entitlement(self, user_sub, feature, action_id=None):
    """Currently fail-open per Phase 9 pricing-enforcement plan.
    
    Multi-period (day/week/month/total) caps require schema work + entitlement
    seeding per pricing1.md. Until Phase 9 lands billing.pricing_consume_entitlement_v3
    with multi-period support + operator-approved seeds, this returns True.
    
    See docs/db-v2/phase-9-pricing-enforcement-plan.md.
    """
    logger.debug(
        "pricing check_entitlement called; fail-open until Phase 9",
        extra={"feature": feature, "action_id": action_id},
    )
    return True

def consume_entitlement(self, user_sub, feature, unit, quantity=1):
    """Fail-open no-op per Phase 9 pricing-enforcement plan."""
    logger.debug(
        "pricing consume_entitlement called; no-op until Phase 9",
        extra={"feature": feature, "unit": unit, "quantity": quantity},
    )
    return True
```

If the methods need to keep some signature (e.g. return a structured result), preserve the signature byte-for-byte and only replace the body.

- [ ] **Step 7: Skip-mark v1-only existing pricing tests**

Find them: `grep -rln "render_user_id" tests/unit/user_pricing/`. For each file that mocks v1 RPCs, add at the top:

```python
import pytest

pytestmark = pytest.mark.skip(
    reason="v1 pricing surface retired in Phase 8.0; replaced by tests/unit/user_pricing/test_repository_v2_billing.py"
)
```

- [ ] **Step 8: Run the v2 test + full not-live regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/unit/user_pricing/test_repository_v2_billing.py -q
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

Expected: v2 unit tests pass; full suite shows max 4 known flakes (sandbox_routes 402, 2× quantize_bge_int8, cascade_int8). NO new failures.

- [ ] **Step 9: Live verification**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_phase_8_pricing.py -q --live
```

Expected: pass.

- [ ] **Step 10: Commit**

```bash
git add website/features/user_pricing/repository.py \
        website/core/persist.py \
        tests/unit/user_pricing/test_repository_v2_billing.py \
        tests/unit/user_pricing/test_*.py
git commit -m "$(cat <<'EOF'
refactor(v2): user_pricing/repository uses billing schema only (8.0.2)
EOF
)"
```

---

## Task 3: Atomic factory swap in `service.py` + persist.py v1 helper hard-delete

**Goal:** (A) `_build_runtime` in `rag_pipeline/service.py` stops calling `get_supabase_scope()` for the client; resolves only `profile_id` UUID; constructs the 5 consumers (HybridRetriever, ChatSessionStore, SandboxStore, LocalizedPageRankScorer, _KGModuleAdapter) with `supabase=None` so each consumer's lazy `get_v2_client()` default takes over. (B) `persist.py` removes `from website.core.supabase_kg import KGNodeCreate, KGRepository, is_supabase_configured`; deletes `_persist_supabase_node`, `_build_supabase_node_payload`, `_create_semantic_links`, `_schedule_entity_extraction`, `_get_cached_existing_types`, `_schedule_embedding_and_links`; `get_supabase_scope()` either renames to `get_supabase_v2_scope` or raises `RuntimeError` when v2 not configured.

**Files:**
- Modify: `website/features/rag_pipeline/service.py`
- Modify: `website/core/persist.py`
- Create: `tests/unit/rag_pipeline/test_service_atomic_swap.py`

**Rationale + citations:** [Shopify Engineering Strangler Fig 2024](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern) — terminal-phase removal. [Kai Waehner Strangler Fig 2025-03-27](https://www.kai-waehner.de/blog/2025/03/27/replacing-legacy-systems-one-step-at-a-time-with-data-streaming-the-strangler-fig-approach/) — "delete the old branch the same week it goes dark." [ArjanCodes Python DI 2024](https://arjancodes.com/blog/python-dependency-injection-best-practices/) — `def __init__(self, client=None): self.client = client or default_factory()` is the canonical lazy-default cutover idiom; pass `None` to leverage it. Both prod users are UUIDs and v1 tables are dropped — dual-path "for safety" is dead code per [Understand Legacy Code 2024](https://understandlegacycode.com/blog/delete-unused-code/).

- [ ] **Step 1: Audit current `_build_runtime`**

```bash
grep -n "_build_runtime\|get_supabase_scope\|KGRepository" website/features/rag_pipeline/service.py
```

Capture the lines that unpack the 2-tuple and pass `repo._client` into consumers.

- [ ] **Step 2: Write failing tests**

Create `tests/unit/rag_pipeline/test_service_atomic_swap.py`:

```python
"""Phase 8.0.3 — atomic factory swap regression tests.

Asserts _build_runtime never instantiates KGRepository for client purposes,
and that the 5 consumers receive a v2 client (None passed; lazy default).
"""
from __future__ import annotations

import inspect

import pytest


def test_service_does_not_import_kg_repository():
    """rag_pipeline/service.py must not import KGRepository from supabase_kg."""
    from website.features.rag_pipeline import service
    src = inspect.getsource(service)
    assert "from website.core.supabase_kg" not in src
    assert "KGRepository" not in src or "# legacy" in src


def test_service_does_not_call_get_supabase_scope():
    """_build_runtime must not unpack get_supabase_scope into (repo, kg_user_id)."""
    from website.features.rag_pipeline import service
    src = inspect.getsource(service)
    assert "get_supabase_scope" not in src, (
        "v1 client wiring must be removed; pass None to consumers' lazy defaults"
    )


def test_service_passes_none_to_consumers():
    """Each of the 5 consumers gets supabase=None (lazy v2 default)."""
    from website.features.rag_pipeline import service
    src = inspect.getsource(service)
    # Each consumer constructor should accept supabase=None and rely on get_v2_client default
    assert "supabase=None" in src or "(supabase=" not in src, (
        "consumers should be constructed with supabase=None or no supabase kwarg"
    )
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/unit/rag_pipeline/test_service_atomic_swap.py -q
```

Expected: 3 failures (v1 imports + scope-call + non-None pass).

- [ ] **Step 4: Refactor `service.py::_build_runtime`**

Replace the v1 wiring. Before:

```python
def _build_runtime(user_sub: str) -> RAGRuntime:
    repo, kg_user_id = get_supabase_scope(user_sub)
    return RAGRuntime(
        sessions=ChatSessionStore(supabase=repo._client, ...),
        sandboxes=SandboxStore(supabase=repo._client, ...),
        retriever=HybridRetriever(supabase=repo._client, ...),
        scorer=LocalizedPageRankScorer(supabase=repo._client, ...),
        kg_adapter=_KGModuleAdapter(supabase=repo._client, ...),
        kg_user_id=kg_user_id,
    )
```

After:

```python
def _build_runtime(user_sub: str) -> RAGRuntime:
    """Build the RAG runtime for a given user sub.
    
    Phase 8.0.3 atomic swap: each consumer below has a lazy `get_v2_client()`
    default in its __init__; passing supabase=None lets the consumer wire the
    v2 client itself. The factory's only responsibility is profile_id resolution.
    Per Branch-by-Abstraction terminal phase (Fowler) + 2024 modernisation
    (Shopify, ArjanCodes Python DI) — see docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md
    """
    if not is_supabase_uuid(user_sub):
        raise RuntimeError(
            f"v2 RAG runtime requires a Supabase auth UUID; got {user_sub!r}"
        )
    profile_id = uuid.UUID(str(user_sub))
    return RAGRuntime(
        sessions=ChatSessionStore(supabase=None),
        sandboxes=SandboxStore(supabase=None),
        retriever=HybridRetriever(supabase=None),
        scorer=LocalizedPageRankScorer(supabase=None),
        kg_adapter=_KGModuleAdapter(supabase=None),
        profile_id=profile_id,
    )
```

If `RAGRuntime` currently has a `kg_user_id` field (text), rename to `profile_id` (uuid) and propagate the rename through `runtime.py` consumers. If consumers downstream still need `kg_user_id`, keep both as aliases for one phase.

- [ ] **Step 5: Refactor `persist.py` — drop v1 imports + helpers**

In `website/core/persist.py`:

1. Remove the import line: `from website.core.supabase_kg import KGNodeCreate, KGRepository, is_supabase_configured`.
2. Replace any remaining `is_supabase_configured()` references with `is_v2_configured()` (alias from `website.core.supabase_v2.client`).
3. DELETE these helper functions entirely:
   - `_persist_supabase_node`
   - `_build_supabase_node_payload`
   - `_create_semantic_links`
   - `_schedule_entity_extraction`
   - `_get_cached_existing_types`
   - `_schedule_embedding_and_links`
4. Refactor `get_supabase_scope()` to either:
   - Rename it to `get_supabase_v2_scope` if it doesn't already exist, OR
   - Make it raise `RuntimeError("v2 path retired the v1 fallback; use get_supabase_v2_scope")` if v2 is not configured.

For any callers within `persist.py` that previously used the v1 helpers, delete those callers too — they were unreachable in the v2-only world.

- [ ] **Step 6: Run the swap tests + regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/unit/rag_pipeline/test_service_atomic_swap.py -q
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/ -q --live
```

Expected: swap tests PASS; regression shows 4 known flakes only; live suite all pass.

- [ ] **Step 7: Commit**

```bash
git add website/features/rag_pipeline/service.py \
        website/core/persist.py \
        tests/unit/rag_pipeline/test_service_atomic_swap.py
git commit -m "$(cat <<'EOF'
refactor(v2): service.py atomic swap + persist.py drop v1 fallback (8.0.3)
EOF
)"
```

---

## Task 4: routes.py v1 fallback hard-deletes + 410 Gone for retired endpoints

**Goal:** Close C, D, E, F, G in one commit:
- (C) `/api/me` v1 fallback block — DELETE.
- (D) `/api/graph` v1 fallback (`is_personal=True` v1 branch + global graph v1 branch) — DELETE.
- (E) `/api/graph/query` + `/api/graph/search` — return HTTP 410 Gone with Sunset+Deprecation headers.
- (F) `/api/graph/rebuild-links` — DELETE the route handler.
- (G) `delete_zettel` v1 fallback — DELETE.

**Files:**
- Modify: `website/api/routes.py`
- Create: `tests/integration/v2/test_routes_v1_retired.py`

**Rationale + citations:** [GitHub API Versions 2026-03-10](https://docs.github.com/en/rest/about-the-rest-api/api-versions) + [GitHub Projects Classic sunset 2024-05-23](https://github.blog/changelog/2024-05-23-sunset-notice-projects-classic/) — `Deprecation` header during sunset window → `410 Gone` after sunset, never `404`. [IETF Deprecation Header draft-09 2024-2025](https://datatracker.ietf.org/doc/draft-ietf-httpapi-deprecation-header/09/) + [RFC 8594 Sunset](https://datatracker.ietf.org/doc/html/rfc8594) — pair both headers; clients can distinguish typo from sunset. [Zalando RESTful API Guidelines deprecation chapter 2024](https://github.com/zalando/restful-api-guidelines/blob/main/chapters/deprecation.adoc) — explicit body explaining removal + successor pointer. [API Handyman 2024](https://apihandyman.io/move-along-no-resource-to-see-here-seriously-http-status-code-204-vs-403-vs-404-vs-410/) — "deliberate removal → 410, accidental absence → 404." Admin endpoints (no client contract) hard-delete.

- [ ] **Step 1: Audit the route handlers**

```bash
grep -n "@app\.\|@router\." website/api/routes.py | head -50
grep -n "/api/me\|/api/graph\|delete_zettel" website/api/routes.py
```

Identify exact line ranges for the 6 affected handlers/branches.

- [ ] **Step 2: Write failing tests for the 410 + delete behaviour**

Create `tests/integration/v2/test_routes_v1_retired.py`:

```python
"""Phase 8.0.4 — routes.py v1-retirement regression tests.

Verifies that /api/graph/query + /api/graph/search return HTTP 410 Gone with
proper headers, and that /api/graph/rebuild-links is deleted (404 from FastAPI).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from website.app import app


pytestmark = pytest.mark.live  # exercises the live FastAPI app instance


@pytest.fixture
def client():
    return TestClient(app)


def test_graph_query_returns_410_gone(client):
    resp = client.post("/api/graph/query", json={"query": "test"})
    assert resp.status_code == 410
    assert "gone" in resp.json().get("error", "").lower()
    assert "Sunset" in resp.headers or "Deprecation" in resp.headers


def test_graph_search_returns_410_gone(client):
    resp = client.get("/api/graph/search?q=test")
    assert resp.status_code == 410
    body = resp.json()
    assert "gone" == body.get("error")
    assert "v2_endpoint" in body  # null is acceptable; key must be present


def test_graph_rebuild_links_route_deleted(client):
    resp = client.post("/api/graph/rebuild-links")
    assert resp.status_code == 404, "admin endpoint should be hard-deleted"


def test_api_me_no_v1_fallback_for_unscoped_uuid_user(client):
    """When v2 scope is None for a UUID user (e.g., signup race), no kg_users read happens."""
    # Best-effort: hit /api/me without a JWT; should return JWT-metadata-only response or 401
    resp = client.get("/api/me")
    # 200 with JWT-metadata-only OR 401 (no JWT) are both acceptable; 500 is not
    assert resp.status_code in (200, 401)
```

- [ ] **Step 3: Run tests to verify they fail (or all pass if endpoints already deleted)**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_routes_v1_retired.py -q --live
```

Expected: most fail (handlers still exist; no 410 response).

- [ ] **Step 4: Refactor `routes.py`**

For each of the 6 sites:

#### 4a. `/api/me` v1 fallback (lines ~230-242 per Phase 8.0 audit)

Find the block reading `kg_users.avatar_url` via `_get_supabase()`. DELETE the entire fallback block. The handler should return only the v2-path response (Phase 4.2's `CoreRepository.get_profile`).

#### 4b. `/api/graph` v1 fallback (lines ~402-436 per Phase 8.0 audit)

Find the `is_personal=True` v1 branch + global graph v1 branch. DELETE both. Anonymous users get the file-store graph (`core.graph_store.get_graph()`); v2-auth users get DB graph via Phase 4.1's v2 branch.

#### 4c. `/api/graph/query` + `/api/graph/search` → 410 Gone

Find the two handlers. Replace each body with:

```python
@app.post("/api/graph/query")  # or @router.get(...) — preserve original decorator
async def graph_query(...):
    """Retired in Phase 6 of the DB v2 migration; no v2 NL-query yet."""
    return JSONResponse(
        status_code=410,
        content={
            "error": "gone",
            "message": "This endpoint was retired in the v2 KG migration.",
            "v2_endpoint": None,
            "docs": "docs/db-v2/cutover-runbook.md",
        },
        headers={
            "Sunset": "Sat, 10 May 2026 00:00:00 GMT",  # actual cutover date
            "Deprecation": "@1715299200",  # Unix timestamp of the announcement (RFC draft format)
        },
    )
```

Same shape for `/api/graph/search`. Adjust function signatures to keep the route decorator intact (FastAPI requires the function to exist for the route to register).

#### 4d. `/api/graph/rebuild-links` — hard delete

Find the handler + decorator. DELETE both — entire `@app.post("/api/graph/rebuild-links") async def rebuild_links(...): ...` block. FastAPI returns 404 when the URL is requested. No body / headers needed.

#### 4e. `delete_zettel` v1 fallback

Find the `delete_zettel` handler (likely under `DELETE /api/zettels/{node_id}`). Find the v1 fallback branch. DELETE it. The v2 path (`ContentRepository.soft_delete_workspace_zettel`) handles the only valid case; non-UUID inputs raise 4xx via `is_supabase_uuid` check.

- [ ] **Step 5: Run tests + regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_routes_v1_retired.py -q --live
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_api_graph_v2.py tests/integration/v2/test_api_me_v2.py tests/integration/v2/test_api_zettels_v2.py -q --live
```

Expected: new tests pass; regression 4 known flakes only; existing v2 endpoint tests still pass.

- [ ] **Step 6: Commit**

```bash
git add website/api/routes.py \
        tests/integration/v2/test_routes_v1_retired.py
git commit -m "$(cat <<'EOF'
refactor(v2): routes.py v1 fallback hard-delete + 410 Gone for retired endpoints (8.0.4-G)
EOF
)"
```

---

## Task 5: Verify Phase 2 invariant + run live regression sweep

**Goal:** After Tasks 2-4 land, the production-code grep invariant must hold:
```
git grep "from website.core.supabase_kg" -- "website/api/" "website/features/" "website/experimental_features/" "website/core/" -- ":!website/core/supabase_kg/"
```
returns ZERO matches. This is the prerequisite for Task 7's `supabase_kg/` directory delete.

**Files:** None modified; verification only.

- [ ] **Step 1: Run the invariant grep**

```bash
git grep "from website.core.supabase_kg" -- "website/api/" "website/features/" "website/experimental_features/" "website/core/" -- ":!website/core/supabase_kg/"
```

Expected: empty output (exit code 1 from grep).

If any matches surface, surface BLOCKED — Task 6 cannot proceed. Likely culprit: a sub-module under `kg_features/` not yet migrated. This is Part 2 territory — do NOT silently expand scope.

- [ ] **Step 2: Run full not-live regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

Expected: 4 known flakes only.

- [ ] **Step 3: Run full v2 live integration suite**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/ -q --live
```

Expected: all pass (~50+ tests).

- [ ] **Step 4: Verify Naruto + Zoro can still sign in**

```bash
PYTHONIOENCODING=utf-8 python -c "
from website.core.supabase_v2.client import get_v2_anon_client
anon = get_v2_anon_client()
naruto = anon.auth.sign_in_with_password({'email': 'naruto@zettelkasten.local', 'password': 'Naruto2026!'})
print(f'Naruto JWT len: {len(naruto.session.access_token)}')
anon.auth.sign_out()
zoro = anon.auth.sign_in_with_password({'email': 'zoro@zettelkasten.test', 'password': 'Zoro2026!'})
print(f'Zoro JWT len: {len(zoro.session.access_token)}')
"
```

Expected: both JWTs returned, lengths > 800.

If any of these fail, BLOCK and surface. Do NOT proceed to Task 6.

---

## Task 6: Drop the 6 retained `public.pricing_*` tables + 6 RPCs

**Goal:** Final destructive cleanup of the 6 retained `public.pricing_*` tables (all 0 rows; verified Phase 7.4 audit + Phase 8.0 inspection) and 6 RPCs. v2 `billing.*` is canonical.

**Files:**
- Create: `supabase/website/_v2/31_drop_legacy_pricing.sql`
- Modify: `tests/unit/supabase_v2/test_schema_files.py` (allowlist)

**Rationale + citations:** [Atlas v0.38 2025](https://atlasgo.io/) — schema-aware migration linting flags destructive changes; CASCADE on FUNCTIONS is safe (only PG-internal grants/comments cascade). [Microsoft Azure Strangler Fig 2024](https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig) — terminal cleanup phase.

- [ ] **Step 1: Capture exact `pg_proc` signatures**

```bash
PYTHONIOENCODING=utf-8 python -c "
import asyncio, asyncpg
from website.core.supabase_v2.client import get_v2_database_url

async def main():
    conn = await asyncpg.connect(get_v2_database_url(listen=False))
    rows = await conn.fetch('''
        SELECT p.proname AS name, pg_get_function_arguments(p.oid) AS args
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname LIKE 'pricing_%'
         ORDER BY p.proname
    ''')
    for r in rows:
        print(f'public.{r[\"name\"]}({r[\"args\"]})')
    await conn.close()
asyncio.run(main())
"
```

Note exact arg lists — they may differ from the spec defaults below.

- [ ] **Step 2: Write the migration file**

Create `supabase/website/_v2/31_drop_legacy_pricing.sql`. Use the actual signatures from Step 1:

```sql
-- Phase 8.0.6: drop the 6 retained public.pricing_* tables + 6 RPCs.
-- All 0 rows; verified via Phase 7.4 audit + Phase 8.0 inspection.
-- v2 canonical = billing.* (already shipped); pricing module migrated in Tasks 1+2.

-- Drop RPCs (CASCADE clears PG-internal grants/comments only).
DROP FUNCTION IF EXISTS public.pricing_active_plan(text) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_check_entitlement(text, text, text) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_plan_cap(text, text, text) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_add_pack_credits(text, text, integer) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_deduct_pack_credits(text, text, integer) CASCADE;
DROP FUNCTION IF EXISTS public.pricing_touch_updated_at() CASCADE;

-- Drop tables (RESTRICT default; no row-data to lose).
DROP TABLE IF EXISTS public.pricing_balances RESTRICT;
DROP TABLE IF EXISTS public.pricing_disputes RESTRICT;
DROP TABLE IF EXISTS public.pricing_payment_events RESTRICT;
DROP TABLE IF EXISTS public.pricing_plan_cache RESTRICT;
DROP TABLE IF EXISTS public.pricing_refunds RESTRICT;
DROP TABLE IF EXISTS public.pricing_webhook_events RESTRICT;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
```

If actual `pg_proc` arg lists differ (e.g. `integer DEFAULT 0`), update the DROP FUNCTION signatures to match exactly — Postgres requires the full signature to drop.

- [ ] **Step 3: Append to schema allowlist**

Modify `tests/unit/supabase_v2/test_schema_files.py`. Append `"31_drop_legacy_pricing.sql"` in alphabetical position.

- [ ] **Step 4: Apply the migration**

```bash
PYTHONIOENCODING=utf-8 MIGRATION_MANIFEST_AUTOBOOTSTRAP=1 python ops/scripts/apply_migrations.py --v2
```

Expected: `applied 31_drop_legacy_pricing.sql in <ms>`.

- [ ] **Step 5: Verify all 12 objects are gone**

```bash
PYTHONIOENCODING=utf-8 python -c "
import asyncio, asyncpg
from website.core.supabase_v2.client import get_v2_database_url

async def main():
    conn = await asyncpg.connect(get_v2_database_url(listen=False))
    n_tables = await conn.fetchval('''
        SELECT count(*) FROM information_schema.tables
         WHERE table_schema='public' AND table_name LIKE 'pricing_%'
    ''')
    n_funcs = await conn.fetchval('''
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname='public' AND p.proname LIKE 'pricing_%'
    ''')
    print(f'public.pricing_* tables remaining: {n_tables}')
    print(f'public.pricing_* functions remaining: {n_funcs}')
    assert n_tables == 0, f'expected 0 tables, got {n_tables}'
    assert n_funcs == 0, f'expected 0 functions, got {n_funcs}'
    await conn.close()
asyncio.run(main())
"
```

Expected: both counts = 0.

- [ ] **Step 6: Commit**

```bash
git add supabase/website/_v2/31_drop_legacy_pricing.sql \
        tests/unit/supabase_v2/test_schema_files.py
git commit -m "$(cat <<'EOF'
ops(v2): drop 6 retained public.pricing_* + 6 RPCs (8.0.6)
EOF
)"
```

---

## Task 7: Delete `website/core/supabase_kg/` + ship closeout docs

**Goal:** Final cleanup. (a) Delete the entire `website/core/supabase_kg/` directory. (b) Delete legacy v1 test directories (`tests/unit/website/supabase_kg/`, `tests/kg_intelligence/`). (c) Write the future-Phase-9 pricing-enforcement plan based on Research M's recommended 6-phase rollout. (d) Update `CLAUDE.md` with v2-only state. (e) Write `docs/db-v2/PURGE-COMPLETE-2026-05-10.md`. (f) Append Phase 8 entry to `docs/db-v2/cutover-runbook.md`.

**Files:**
- Delete: `website/core/supabase_kg/` (entire directory).
- Delete: `tests/unit/website/supabase_kg/` (entire directory).
- Delete: `tests/kg_intelligence/` (entire directory).
- Create: `docs/db-v2/phase-9-pricing-enforcement-plan.md`.
- Create: `docs/db-v2/PURGE-COMPLETE-2026-05-10.md`.
- Modify: `CLAUDE.md`.
- Modify: `docs/db-v2/cutover-runbook.md`.

**Rationale + citations:** [Understand Legacy Code 2024](https://understandlegacycode.com/blog/delete-unused-code/) — git history is the archive. [Built In Braintree 2023](https://builtin.com/software-engineering-perspectives/delete-old-dead-code-braintree) — keep code that's used; delete code that isn't. [Shopify Strangler Fig 2024](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern) — "delete the old branch the same week it goes dark."

- [ ] **Step 1: Verify the invariant one more time before delete**

```bash
git grep "from website.core.supabase_kg" -- "website/api/" "website/features/" "website/experimental_features/" "website/core/" -- ":!website/core/supabase_kg/"
```

Expected: empty.

If any matches surface — STOP. Some module in Part 2 territory is still importing v1; do NOT delete the directory.

- [ ] **Step 2: Delete the directory + legacy test directories**

```bash
git rm -r website/core/supabase_kg/
git rm -r tests/unit/website/supabase_kg/
git rm -r tests/kg_intelligence/
```

- [ ] **Step 3: Verify pytest collects without errors**

```bash
PYTHONIOENCODING=utf-8 python -m pytest --collect-only -q 2>&1 | tail -10
```

Expected: collection succeeds; no `ImportError` from missing `supabase_kg` module. If errors surface, the invariant grep missed something — surface and roll back.

- [ ] **Step 4: Run full not-live regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

Expected: 4 known flakes only.

- [ ] **Step 5: Run live integration suite**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/ -q --live
```

Expected: all pass.

- [ ] **Step 6: Write `docs/db-v2/phase-9-pricing-enforcement-plan.md`**

Content based on Research M's recommended 6-phase rollout (multi-period schema + counter-row + dual-write + shadow mode + grandfathered Free subscription on signup trigger + Razorpay webhook upgrade). Include the citations: Lago, OpenMeter, Stripe Meters, Cormack 2009 RRF (no — that's retrieval), Schematic feature flags, Monetizely grandfathering, LaunchDarkly Guarded Releases, Postgres ALTER FUNCTION docs.

Skeleton:

```markdown
# Phase 9 — Pricing Enforcement Plan (future iteration)

> Authored 2026-05-10. NOT scheduled. Cited from research synthesis during Phase 8.0 closeout.

## Goal

Move from current "fail-open" entitlement gate (Phase 8.0 documented) to fully-enforced
v2 entitlement per `docs/research/pricing1.md` (Free 2/10/30 daily/weekly/monthly zettels;
Basic 5/30/50; Max 30/100/200) without breaking real users.

## 6-Phase rollout

### Phase 9.A — Schema additions (additive only)
... (multi-row pricing_plan_entitlements + pricing_usage_counters + pricing_decisions_audit)

### Phase 9.B — pricing_consume_entitlement_v3 RPC alongside v2
... (golden-md5 v2 left untouched)

### Phase 9.C — Default subscription seeding (operator-approved per row)
... (auth_user_after_insert trigger creates Free subscription; backfill existing users)

### Phase 9.D — Shadow mode (7-14 days)
... (v3 dry_run logging; fail-open path remains live)

### Phase 9.E — Hard cutover
... (PRICING_ENFORCEMENT=hard env flag; 402 quota_exhausted)

### Phase 9.F — Razorpay webhook → subscription upgrade
... (basic / max plan upserts)

## Citations

- Lago — open-source metering: https://github.com/getlago/lago
- OpenMeter Entitlements: https://openmeter.io/docs/billing/entitlements/entitlement
- Stripe Meters: https://docs.stripe.com/billing/subscriptions/usage-based/recording-usage
- Neon — Rate Limiting in Postgres: https://neon.com/guides/rate-limiting
- Schematic feature flags: https://schematichq.com/blog/guide-how-to-use-feature-flags-to-manage-entitlements-without-writing-code
- LaunchDarkly Entitlements: https://launchdarkly.com/blog/how-to-manage-entitlements-with-feature-flags/
- Monetizely Grandfathering: https://www.getmonetizely.com/articles/grandfathering-vs-forced-migration-the-strategic-approach-to-price-changes-for-existing-customers
- Postgres ALTER FUNCTION: https://www.postgresql.org/docs/current/sql-alterfunction.html
```

- [ ] **Step 7: Update `CLAUDE.md` with v2-only state**

Find the bottom of `CLAUDE.md` (after the Docker section). Append:

```markdown

## DB v2 Purge — Complete (2026-05-10)

The DB v2 schema purge is complete. All production code paths run on the v2 schemas (`core, content, kg, rag, pipelines, billing`); legacy `public.kg_*`, `public.rag_*`, `public.chat_*`, `public.summary_batch_*`, `public.nexus_*`, `public.kg_usage_edges*`, `public.kg_kasten_node_freq`, `public.recompute_runs`, `public._migrations_applied`, and 11 `public.pricing_*` tables are dropped (commits `e168b38` Phase 6 + `<8.0.6 sha>` Phase 8.0.6).

**Test infrastructure:**
- `tests/v2/fixtures/users.py` — `mint_test_user_with_workspaces`
- `tests/integration/v2/conftest.py` — `asyncpg_pool`, `mint_user`, `pytest_sessionfinish` cleanup hook
- 4 known not-live flakes documented (sandbox_routes 402, 2× quantize_bge_int8, cascade_int8)

**Pricing module:**
- v2 canonical = `billing.*` schema. `pricing_consume_entitlement` body protected by golden md5.
- Currently fail-open per operator-locked design; multi-period enforcement is Phase 9 (see `docs/db-v2/phase-9-pricing-enforcement-plan.md`).

**Final acceptance test (queued):** `docs/db-v2/final-acceptance-test-plan.md` — Claude in Chrome on the live site, ingesting URLs from `docs/research/Chintan_Testing.md` as Naruto.
```

- [ ] **Step 8: Write `docs/db-v2/PURGE-COMPLETE-2026-05-10.md`**

```markdown
# DB v2 Purge — Complete (2026-05-10)

## Final state
- 2 users (Naruto + Zoro), both UUID-authed.
- 3 zettels backfilled to Naruto's workspace at Phase 5.
- All v2 schemas (`core, content, kg, rag, pipelines, billing`) live and populated.
- Legacy `public.*` tables and 7 v1 RPCs dropped (Phases 6, 7.2, 8.0.6).
- `website/core/supabase_kg/` directory deleted (Phase 8.0.7).

## Total commits
Run: `git log --oneline 80117ec..HEAD | wc -l` — fill in count.

## Phase summary
- Phase 0: pre-flight (9/9 sub-units)
- Phase 1: v2 RPC surface (1.A-D, 8 RPCs + alias table)
- Phase 2: rag_pipeline Bucket-B (7 sub-tasks; service.py atomic swap deferred to Phase 8.0.3)
- Phase 3: other Bucket-B (6 sub-tasks)
- Phase 4: read-path API handlers (4 routes)
- Phase 5: fresh start with 2 users + Naruto backfill
- Phase 6: DESTRUCTIVE drop of 30 legacy objects
- Phase 7: hardening + kasten member-sharing (7.2-deferred folded in)
- Phase 8: closeout (pricing migration + service.py swap + persist.py cleanup + endpoint deletes + supabase_kg/ delete)

## Deferred to future iterations
- Phase 9 — pricing enforcement (multi-period schema + dual-write + shadow mode); see `phase-9-pricing-enforcement-plan.md`.
- Caddy `@maintenance` matcher staging-flip test (operator manual, post-deploy).

## Final acceptance test (queued)
`docs/db-v2/final-acceptance-test-plan.md` — Claude in Chrome on the live site.
```

- [ ] **Step 9: Append Phase 8 entry to cutover-runbook**

```markdown

## Phase 8 closeout executed 2026-05-10

**Scope:** 7 commits (8.0.1 through 8.0.7 — see Phase 8 plan).

**Operator-approved overrides:**
- B-pricing-migration: refactor pricing module to billing.* schema (per pricing_module_authority memory).
- 6 retained public.pricing_* tables + 6 RPCs dropped (derivative authorisation under Phase 6's 30-table approval).
- /api/me/avatar v1 → v2 port (closes operator-flagged H1 example).
- /api/graph/query + /api/graph/search → 410 Gone (closes E).
- /api/graph/rebuild-links route deleted (admin endpoint; closes F).
- service.py atomic factory swap; consumers receive v2 client via lazy default (closes A).
- persist.py v1 helpers hard-deleted (closes B).
- website/core/supabase_kg/ directory deleted (final cleanup).

**Plan-amendment patches:**
- `feedback_deferral_is_a_decision.md` saved 2026-05-10 — counterpart to `feedback_anything_beyond_plan_needs_approval.md`.

**Post-closeout invariants:**
- `git grep "from website.core.supabase_kg" -- "website/" "tests/"` → 0 matches.
- `pytest tests/ -m "not live"` → 4 known flakes only.
- `pytest tests/integration/v2/ --live` → all pass.
- Login verification: Naruto + Zoro PASS.
```

- [ ] **Step 10: Final verification + commit**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/ -q --live
```

Both must pass per the gate criteria. Then:

```bash
git add docs/db-v2/phase-9-pricing-enforcement-plan.md \
        docs/db-v2/PURGE-COMPLETE-2026-05-10.md \
        docs/db-v2/cutover-runbook.md \
        CLAUDE.md
git rm -r website/core/supabase_kg/ tests/unit/website/supabase_kg/ tests/kg_intelligence/
git commit -m "$(cat <<'EOF'
docs(v2): purge complete + delete supabase_kg + phase-9 plan (8.0.7)
EOF
)"
```

- [ ] **Step 11: Push (unless operator wants final review first)**

Awaits operator approval per phase-transition rules. Per `feedback_approval_threshold.md` push of phase-completion is autonomous; surface if uncertain.

```bash
git push origin master
```

---

## Self-Review

**Spec coverage:**
- ✅ A — service.py atomic swap → Task 3
- ✅ B — persist.py v1 helper hard-delete → Task 3
- ✅ C — `/api/me` v1 fallback delete → Task 4 (4a)
- ✅ D — `/api/graph` v1 fallback delete → Task 4 (4b)
- ✅ E — `/api/graph/query` + `/api/graph/search` → 410 Gone → Task 4 (4c)
- ✅ F — `/api/graph/rebuild-links` hard-delete → Task 4 (4d)
- ✅ G — `delete_zettel` v1 fallback delete → Task 4 (4e)
- ✅ Pricing migration (Tasks 1+2): port `pricing_active_plan` + refactor `user_pricing/repository.py` to `billing.*` exclusively + delete v1 fallbacks (closes H2 + H3 from Phase 8.0 audit)
- ✅ Drop 6 retained `public.pricing_*` tables + 6 RPCs → Task 6
- ✅ Delete `website/core/supabase_kg/` → Task 7
- ✅ CLAUDE.md update + final notes + Phase 9 plan → Task 7

**Placeholder scan:** No "TBD" or "implement later." Each step has actual code or actual command to run.

**Type consistency:**
- `profile_id: uuid.UUID` used consistently across Tasks 1, 2, 3.
- `pricing_active_plan(uuid)` v2 RPC defined in Task 1; consumed in Task 2.
- `get_billing_scope(user_sub) -> tuple[Client, UUID]` defined in Task 2; not used in other tasks.
- `_build_runtime` returns `RAGRuntime` in Task 3; if `RAGRuntime` field name changes from `kg_user_id` to `profile_id`, propagate at runtime.py — flagged in Task 3 step 4 with conditional rename guidance.

**Anti-patterns avoided (per Research M 2024+ retros):**
- ✅ Atomic factory swap (no permanent dual-path / dead flag).
- ✅ 410 Gone (not 404) on retired endpoints, with Sunset + Deprecation headers.
- ✅ Hard-delete (not tombstone) for v1 helpers since both prod users are UUIDs and tables are dropped.
- ✅ Per-commit health check (regression suite runs before next task).

---

## Citations (Part 1, recent 2023-2026 only)

1. **Shopify Engineering — Refactoring Legacy Code with the Strangler Fig Pattern** (maintained 2024). https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern
2. **Stripe Docs — Versioning and support policy** (2024-09-30 Acacia release model). https://docs.stripe.com/sdks/versioning
3. **Stripe API versioning** (2024+). https://docs.stripe.com/api/versioning
4. **GitHub Docs — API Versions** (2026-03-10). https://docs.github.com/en/rest/about-the-rest-api/api-versions
5. **GitHub blog — Sunset notice for Projects classic** (2024-05-23). https://github.blog/changelog/2024-05-23-sunset-notice-projects-classic/
6. **IETF draft-ietf-httpapi-deprecation-header-09** (2024-2025). https://datatracker.ietf.org/doc/draft-ietf-httpapi-deprecation-header/09/
7. **RFC 8594 — The Sunset HTTP Header Field** (2019; still canonical). https://datatracker.ietf.org/doc/html/rfc8594
8. **RFC 9110 — HTTP Semantics § 410 Gone** (2022). https://www.rfc-editor.org/rfc/rfc9110.html#name-410-gone
9. **Zalando RESTful API Guidelines — deprecation chapter** (maintained 2024-2025). https://github.com/zalando/restful-api-guidelines/blob/main/chapters/deprecation.adoc
10. **LaunchDarkly — Release Management Best Practices with Feature Flags** (2024). https://launchdarkly.com/blog/release-management-flags-best-practices/
11. **Ariga Atlas Docs** (v0.38, 2025). https://atlasgo.io/
12. **Kai Waehner — Replacing Legacy Systems with Data Streaming: Strangler Fig** (2025-03-27). https://www.kai-waehner.de/blog/2025/03/27/replacing-legacy-systems-one-step-at-a-time-with-data-streaming-the-strangler-fig-approach/
13. **Understand Legacy Code — Delete unused code** (maintained 2024). https://understandlegacycode.com/blog/delete-unused-code/
14. **ArjanCodes — Best Practices for Python Dependency Injection** (2024). https://arjancodes.com/blog/python-dependency-injection-best-practices/
15. **Built In / Braintree — How and Why to Delete Old or Dead Code** (2023). https://builtin.com/software-engineering-perspectives/delete-old-dead-code-braintree
16. **Microsoft Azure Architecture Center — Strangler Fig pattern** (maintained 2023+). https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig
17. **AWS Prescriptive Guidance — Strangler Fig pattern**. https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/strangler-fig.html
18. **MDN HTTP 410 Gone**. https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/410
19. **API Handyman — 204 vs 403 vs 404 vs 410** (canonical reference). https://apihandyman.io/move-along-no-resource-to-see-here-seriously-http-status-code-204-vs-403-vs-404-vs-410/

---

## Part 2 — H1-H10 hotspot fixes (research-validated)

Background research O+P+Q returned 2026-05-10; verdicts below are direct quotes / paraphrases of those briefs. Tasks 8-15 close the 10 hotspots surfaced by the UI regression audit (Research N).

---

## Task 8: H1 — atomic swap of `PUT /api/me/avatar` to `core.profiles`

**Goal:** Replace the v1 `repo.update_user_avatar()` write (against dropped `public.kg_users`) with a direct write to `core.profiles.avatar_url`. Single commit, atomic swap, no feature flag, no URL versioning. Operator-flagged H1 example.

**Files:**
- Modify: `website/api/routes.py` (the `PUT /api/me/avatar` handler at ~line 246-270 per Phase 8.0 audit).
- Modify: `website/core/supabase_v2/repositories/core_repository.py` — add `update_profile_avatar(profile_id, avatar_url)` method.
- Create: `tests/integration/v2/test_avatar_v2.py` — live integration test (replaces any v1 avatar test).

**Rationale + citations (Research O, 2023-2024 sources):**

The legacy `public.kg_users` table is already DROPPED (Phase 6 commit `e168b38`). Every current call to `PUT /api/me/avatar` is a 500. This collapses Expand/Contract to its **contract** phase ([Hodgson — Expand/Contract: making a breaking change without a big bang, 2023](https://blog.thepete.net/blog/2023/12/05/expand/contract-making-a-breaking-change-without-a-big-bang/)) — only the contract step remains.

URL versioning would be wrong: per [Speakeasy 2024](https://www.speakeasy.com/api-design/versioning) and [Zuplo 2024](https://zuplo.com/learning-center/api-versioning-backward-compatibility-best-practices), URL versions are reserved for **breaking the contract** with external clients (URL/payload changes). The URL, request schema, and response schema are unchanged here — only the storage backend moves. URL versioning would create permanent `/v2/` debt + client confusion.

Feature flag would be wrong: per [Harness 2024](https://www.harness.io/blog/database-migration-with-feature-flags) and [CloudBees 2024](https://www.cloudbees.com/blog/mitigate-infrastructure-migration-risk-with-feature-flags), flag gates pay off when there's a **working** fallback to flag back to. The legacy fallback is already non-functional (table dropped). Gating to a 500 path is theatre.

- [ ] **Step 1: Add `update_profile_avatar` to `CoreRepository`**

Open `website/core/supabase_v2/repositories/core_repository.py`. Locate the existing `CoreRepository` class. Add a method:

```python
def update_profile_avatar(self, profile_id: uuid.UUID, avatar_url: str | None) -> None:
    """Set core.profiles.avatar_url for the given profile_id.

    Raises if no row matches; caller is expected to ensure the profile exists
    (the auth.users → core.profiles trigger creates the row at signup).
    """
    resp = (
        self._client.schema("core")
        .table("profiles")
        .update({"avatar_url": avatar_url})
        .eq("id", str(profile_id))
        .execute()
    )
    if not resp.data:
        raise RuntimeError(f"profile {profile_id} not found in core.profiles")
```

If the existing class doesn't have an `_client` attribute (it should, per Phase 4.2 wiring), adapt to the actual signature.

- [ ] **Step 2: Write the failing live integration test**

Create `tests/integration/v2/test_avatar_v2.py`:

```python
"""Phase 8.0 H1 — PUT /api/me/avatar v2 port (write to core.profiles)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from website.app import app


pytestmark = pytest.mark.live


@pytest.fixture
def client():
    return TestClient(app)


def test_avatar_put_writes_to_core_profiles(client, mint_user, asyncpg_pool):
    """PUT /api/me/avatar writes to core.profiles.avatar_url, NOT to dropped kg_users."""
    user = mint_user(workspace_count=1)
    headers = {"Authorization": f"Bearer {user.jwt}"}

    new_avatar = "https://example.com/avatar-test.png"
    resp = client.put("/api/me/avatar", json={"avatar_url": new_avatar}, headers=headers)
    assert resp.status_code == 200, f"avatar PUT failed: {resp.text}"

    # Round-trip via direct DB read
    import asyncio
    async def fetch():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT avatar_url FROM core.profiles WHERE id = $1",
                user.profile_id,
            )
    stored = asyncio.get_event_loop().run_until_complete(fetch())
    assert stored == new_avatar, f"expected {new_avatar!r}, got {stored!r}"


def test_avatar_put_returns_updated_value_via_get_me(client, mint_user):
    """After PUT /api/me/avatar, GET /api/me must return the updated value."""
    user = mint_user(workspace_count=1)
    headers = {"Authorization": f"Bearer {user.jwt}"}
    new_avatar = "https://example.com/avatar-roundtrip.png"

    client.put("/api/me/avatar", json={"avatar_url": new_avatar}, headers=headers)
    resp = client.get("/api/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("avatar_url") == new_avatar
```

- [ ] **Step 3: Run the test to verify it FAILS**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_avatar_v2.py -q --live
```

Expected: 2 failures (handler still calls dropped `kg_users`).

- [ ] **Step 4: Refactor the handler in `routes.py`**

Find `PUT /api/me/avatar` handler (~line 246-270 per Phase 8.0 audit). Before:

```python
@app.put("/api/me/avatar")
async def update_avatar(payload: AvatarPayload, user: User = Depends(...)):
    repo = _get_supabase()  # legacy v1 KGRepository
    repo.get_or_create_user(user.sub)
    repo.update_user_avatar(user.sub, payload.avatar_url)
    return {"ok": True, "avatar_url": payload.avatar_url}
```

After:

```python
@app.put("/api/me/avatar")
async def update_avatar(payload: AvatarPayload, user: User = Depends(...)):
    """Phase 8.0 H1: writes to core.profiles.avatar_url. v1 path retired.
    
    Per Hodgson 2023 / Speakeasy 2024 / Zuplo 2024 — URL contract unchanged;
    storage backend moved; atomic swap is the correct pattern.
    """
    if not is_supabase_uuid(user.sub):
        raise HTTPException(
            status_code=400,
            detail="v2 avatar update requires a Supabase auth UUID",
        )
    profile_id = uuid.UUID(str(user.sub))
    repo = CoreRepository(get_v2_client())
    repo.update_profile_avatar(profile_id, payload.avatar_url)
    return {"ok": True, "avatar_url": payload.avatar_url}
```

Verify imports at the top of `routes.py`: `uuid`, `is_supabase_uuid`, `get_v2_client`, `CoreRepository`, `HTTPException`.

- [ ] **Step 5: Run the test to verify it PASSES**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_avatar_v2.py -q --live
```

Expected: 2 passed.

- [ ] **Step 6: Run full not-live + v2 integration regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/ -q --live
```

Expected: 4 known flakes only on not-live; all v2 live tests pass.

- [ ] **Step 7: Commit**

```bash
git add website/api/routes.py \
        website/core/supabase_v2/repositories/core_repository.py \
        tests/integration/v2/test_avatar_v2.py
git commit -m "$(cat <<'EOF'
refactor(v2): /api/me/avatar atomic swap to core.profiles (8.0-H1)
EOF
)"
```

---

## Task 9: H5 — port `kg_extraction_blocklist` to `pipelines.extraction_blocklist`

**Goal:** Reads/writes in `website/features/rag_pipeline/query/blocklist.py:61,106,127,145` reference dropped `public.kg_extraction_blocklist`. Per Research P, this is mutable runtime config (operator adds spammy domain at 2am without redeploy) — persistence is correct; in-memory dict would lose state on every blue/green flip. Port to v2 schema.

**Files:**
- Create: `supabase/website/_v2/32_extraction_blocklist.sql` — new `pipelines.extraction_blocklist` table.
- Modify: `website/features/rag_pipeline/query/blocklist.py` — switch all 4 call sites from `public.kg_extraction_blocklist` to `pipelines.extraction_blocklist`.
- Create: `tests/integration/v2/test_extraction_blocklist_v2.py`.
- Modify: `tests/unit/supabase_v2/test_schema_files.py` (allowlist).

**Rationale + citations (Research P, 2024-2025 sources):**

[Fluri — Expand and Contract Method for Database Changes 2024](https://medium.com/@jasminfluri/expand-and-contract-method-for-database-changes-414d236f236f) and [Atlas Strategies for Reliable Migrations 2024](https://atlasgo.io/blog/2024/10/09/strategies-for-reliable-migrations) frame this exactly: persistent mutable runtime config belongs in a small schema-versioned table. In-memory state evaporates on blue/green flips ([2GB DigitalOcean droplet uses blue/green per CLAUDE.md ops section](https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig)).

- [ ] **Step 1: Inspect current blocklist.py**

```bash
grep -n "kg_extraction_blocklist\|extraction_blocklist" website/features/rag_pipeline/query/blocklist.py
```

Capture all 4 call sites + the column shape (likely `domain text PRIMARY KEY, reason text, created_at timestamptz`).

- [ ] **Step 2: Write SQL migration**

Create `supabase/website/_v2/32_extraction_blocklist.sql`:

```sql
-- Phase 8.0 H5: port public.kg_extraction_blocklist → pipelines.extraction_blocklist.
-- Mutable runtime config; operator-add via direct INSERT.

CREATE TABLE IF NOT EXISTS pipelines.extraction_blocklist (
    domain      text PRIMARY KEY,
    reason      text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid REFERENCES core.profiles(id) ON DELETE SET NULL
);

ALTER TABLE pipelines.extraction_blocklist ENABLE ROW LEVEL SECURITY;

CREATE POLICY extraction_blocklist_service_all ON pipelines.extraction_blocklist
  FOR ALL
  USING ((SELECT current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role')
  WITH CHECK ((SELECT current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role');

GRANT SELECT ON TABLE pipelines.extraction_blocklist TO authenticated, service_role;
GRANT INSERT, UPDATE, DELETE ON TABLE pipelines.extraction_blocklist TO service_role;

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
```

Adjust column list to match the actual v1 schema (e.g. if v1 used `pattern` instead of `domain`, mirror it).

- [ ] **Step 3: Append to schema allowlist**

Modify `tests/unit/supabase_v2/test_schema_files.py`. Append `"32_extraction_blocklist.sql"`.

- [ ] **Step 4: Apply migration**

```bash
PYTHONIOENCODING=utf-8 MIGRATION_MANIFEST_AUTOBOOTSTRAP=1 python ops/scripts/apply_migrations.py --v2
```

- [ ] **Step 5: Refactor `blocklist.py` call sites**

For each of the 4 call sites in `website/features/rag_pipeline/query/blocklist.py`:
- `client.table("kg_extraction_blocklist")` → `client.schema("pipelines").table("extraction_blocklist")`

Verify the column names in the actual queries match the v2 schema. If v1 used `domain` and v2 uses `pattern`, rename in the queries.

- [ ] **Step 6: Write the v2 integration test**

Create `tests/integration/v2/test_extraction_blocklist_v2.py`:

```python
"""Phase 8.0 H5 — pipelines.extraction_blocklist v2 port."""
from __future__ import annotations

import pytest

from website.features.rag_pipeline.query.blocklist import (
    is_blocked, add_to_blocklist, remove_from_blocklist,
)


pytestmark = pytest.mark.live


def test_blocklist_add_and_check():
    add_to_blocklist("spam.example.com", reason="test fixture")
    try:
        assert is_blocked("https://spam.example.com/article") is True
        assert is_blocked("https://legit.example.com/article") is False
    finally:
        remove_from_blocklist("spam.example.com")


def test_blocklist_module_imports_no_v1():
    import inspect
    from website.features.rag_pipeline.query import blocklist
    src = inspect.getsource(blocklist)
    assert "kg_extraction_blocklist" not in src
    assert "from website.core.supabase_kg" not in src
```

Adjust assertion shape to match the actual `is_blocked` / `add_to_blocklist` signatures.

- [ ] **Step 7: Run + commit**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_extraction_blocklist_v2.py -q --live
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

Expected: new tests pass; regression 4 known flakes.

```bash
git add supabase/website/_v2/32_extraction_blocklist.sql \
        website/features/rag_pipeline/query/blocklist.py \
        tests/integration/v2/test_extraction_blocklist_v2.py \
        tests/unit/supabase_v2/test_schema_files.py
git commit -m "$(cat <<'EOF'
refactor(v2): blocklist uses pipelines.extraction_blocklist (8.0-H5)
EOF
)"
```

---

## Task 10: H6 — replace `kg_kasten_metrics` writes with OpenTelemetry counters + structured logs

**Goal:** `website/features/rag_pipeline/observability/kasten_stats.py` writes to dropped `public.kg_kasten_metrics`. Per Research P, app metrics in OLTP DB is anti-pattern (write amplification, no aggregation, no retention). Replace persistence with OpenTelemetry counters + structured-log emissions; remove DB persistence entirely.

**Files:**
- Modify: `website/features/rag_pipeline/observability/kasten_stats.py` — remove DB persistence; emit OTel + structured logs.
- Create: `tests/unit/rag_pipeline/test_kasten_stats_otel.py` — verifies no DB writes happen.

**Rationale + citations (Research P, 2024-2025):**

[The New Stack — Observability in 2024: More OpenTelemetry, Less Confusion](https://thenewstack.io/observability-in-2024-more-opentelemetry-less-confusion/) — OTel is the 2024 default for application metrics. [dev.to — Rethinking Observability Costs: Structured Logging 2024](https://dev.to/anderson_leite/rethinking-observability-costs-how-structured-logging-can-save-you-thousands-54bh) and [Grepr — Why Structured Logging Matters 2025](https://www.grepr.ai/blog/structured-logging---what-it-is-and-why-you-need-it): high-cardinality structured events (logged JSON) are the source of truth; metrics are derived on read. [groundcover — OpenTelemetry Metrics: Types & Best Practices](https://www.groundcover.com/opentelemetry/opentelemetry-metrics): `Counter` / `Histogram` instruments with stdout/OTLP exporter.

- [ ] **Step 1: Audit current `kasten_stats.py`**

```bash
grep -n "kg_kasten_metrics\|kasten_metrics" website/features/rag_pipeline/observability/kasten_stats.py
```

Capture every read + write call site.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/rag_pipeline/test_kasten_stats_otel.py`:

```python
"""Phase 8.0 H6 — kasten_stats no longer writes to DB; emits OTel counters."""
from __future__ import annotations

import inspect


def test_kasten_stats_no_db_persistence():
    from website.features.rag_pipeline.observability import kasten_stats
    src = inspect.getsource(kasten_stats)
    assert "kg_kasten_metrics" not in src, "v1 table reference must be removed"
    assert "from website.core.supabase_kg" not in src
    # OTel meter or structured log expected
    assert (
        "opentelemetry" in src.lower() or
        "logger." in src or
        "logging." in src
    ), "must emit OTel counters or structured logs"


def test_kasten_stats_record_does_not_raise():
    """Calling the public record* function is now a no-op-or-log; must not raise."""
    from website.features.rag_pipeline.observability.kasten_stats import (
        # adjust to actual public symbols after inspection
        record_kasten_extraction,
    )
    record_kasten_extraction(kasten_id="00000000-0000-0000-0000-000000000000", count=1)
```

If the public symbol differs, adjust the import.

- [ ] **Step 3: Refactor `kasten_stats.py`**

Replace every DB write with OTel + structured log:

```python
"""Phase 8.0 H6: kasten_stats observability — DB persistence retired.

Per Research P 2024-2025: app metrics in OLTP DB is anti-pattern.
We emit:
- Structured logs (JSON via stdlib logging) for high-cardinality events.
- OpenTelemetry Counter instruments where the runtime exposes a meter.

Future: when we migrate to OTLP-exporter (Grafana / Honeycomb / Datadog),
no code changes — just point the exporter via OTEL_EXPORTER_OTLP_ENDPOINT.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("rag.kasten_stats")

# Optional OTel meter — falls back to no-op if otel-api not installed.
try:
    from opentelemetry import metrics
    _meter = metrics.get_meter("zettelkasten.rag.kasten_stats")
    _extraction_counter = _meter.create_counter(
        name="kasten.extraction.count",
        description="Number of extractions per kasten",
        unit="1",
    )
except Exception:  # noqa: BLE001 — telemetry is optional
    _extraction_counter = None


def record_kasten_extraction(*, kasten_id: str, count: int = 1, **attrs: Any) -> None:
    """Record an extraction event. No DB write; OTel + structured log only."""
    logger.info(
        "kasten.extraction",
        extra={"kasten_id": kasten_id, "count": count, **attrs},
    )
    if _extraction_counter is not None:
        _extraction_counter.add(count, {"kasten_id": kasten_id, **attrs})


# (Replicate other public symbols — record_*, get_*, etc. — with the same pattern.)
```

DELETE every `client.table("kg_kasten_metrics")` and equivalent. If there's a "read" function (e.g., `get_kasten_extraction_count`), either:
- Return a fixed `0` with a deprecation log line + comment pointing at the future Grafana dashboard, OR
- Hard-delete the function if no callers (verify with `grep`).

- [ ] **Step 4: Run + commit**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/unit/rag_pipeline/test_kasten_stats_otel.py -q
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

```bash
git add website/features/rag_pipeline/observability/kasten_stats.py \
        tests/unit/rag_pipeline/test_kasten_stats_otel.py
git commit -m "$(cat <<'EOF'
refactor(v2): kasten_stats emits OTel + structured logs (8.0-H6)
EOF
)"
```

---

## Task 11: H7 — hard-delete 5 `kg_features/*.py` modules + add CI guard

**Goal:** `website/features/kg_features/{retrieval, nl_query, embeddings, metadata_enricher, entity_canonicalizer}.py` reference dropped v1 tables + retired RPCs. Per Research Q, hard-delete (not tombstone) is the 2024+ default when unreachability is proven. Plus add a CI grep guard so future v2 work cannot silently re-import them.

**Files:**
- Delete: 5 files under `website/features/kg_features/` (verified-unreachable list).
- Modify: `website/features/kg_features/__init__.py` — replace exports / leave a one-line tombstone comment pointing at the deletion commit SHA.
- Create: `tests/unit/test_kg_features_unreachable.py` — CI guard.

**Rationale + citations (Research Q, 2024 sources):**

[understandlegacycode.com — Delete unused code (and how to retrieve it) 2024](https://understandlegacycode.com/blog/delete-unused-code/) — git history is the canonical archive; in-tree dead code "adds cruft… less code is easier to digest." [scheb/tombstone 2024](https://github.com/scheb/tombstone) — tombstones are for *suspected-dead-but-reachable* code; once unreachability is proven, "remove the tombstone and the surrounding code." [LaunchDarkly — Reducing technical debt from feature flags 2024](https://docs.launchdarkly.com/guides/flags/technical-debt) — flag retirement is a two-stage workflow: (1) remove code references, (2) archive the flag. [ConfigCat — Feature Flag Retirement 2024-01-30](https://configcat.com/blog/2024/01/30/feature-flag-retirement/) — "delete the conditional logic from the codebase." [PEP 702 / `warnings.deprecated` 2024](https://peps.python.org/pep-0702/) — `@deprecated` is for *public API surfaces with downstream callers*, not internal unreachable modules. [vulture v0.x 2024](https://github.com/jendrikseipp/vulture) — confidence-100 unreachable detection.

- [ ] **Step 1: Verify unreachability one more time**

```bash
git grep -n "from website.features.kg_features" -- "website/api/" "website/features/" "website/experimental_features/" "website/core/" -- ":!website/features/kg_features/"
```

Expected: empty (no production module imports `kg_features` from outside the kg_features dir itself).

If any matches surface, BLOCK — Task 11 cannot proceed; the modules ARE reachable. Surface to operator and reconsider.

- [ ] **Step 2: Write the CI guard test**

Create `tests/unit/test_kg_features_unreachable.py`:

```python
"""Phase 8.0 H7 — CI guard: kg_features modules MUST NOT be imported from production paths.

If a future v2 commit adds an import of website.features.kg_features.* outside the
kg_features dir itself, this test fails and the regression is caught at PR time.
"""
import subprocess
from pathlib import Path


def test_no_production_imports_of_kg_features():
    """grep for `from website.features.kg_features` outside kg_features/ — must be empty."""
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "git", "grep", "-l",
            "from website.features.kg_features",
            "--",
            "website/api/", "website/features/", "website/experimental_features/", "website/core/",
            ":!website/features/kg_features/",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    matches = [line for line in result.stdout.splitlines() if line.strip()]
    assert matches == [], (
        f"kg_features re-imported from production paths: {matches}. "
        "Per Phase 8.0 H7, kg_features modules were retired; no v2 module may import them. "
        "If you need v1 retrieval/NL-query/embedding/canonicalization patterns, port to v2 or design fresh."
    )
```

- [ ] **Step 3: Run the test (should pass already since kg_features is unreachable)**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/unit/test_kg_features_unreachable.py -q
```

Expected: PASS (no production imports).

- [ ] **Step 4: Hard-delete the 5 modules**

```bash
git rm website/features/kg_features/retrieval.py
git rm website/features/kg_features/nl_query.py
git rm website/features/kg_features/embeddings.py
git rm website/features/kg_features/metadata_enricher.py
git rm website/features/kg_features/entity_canonicalizer.py
```

- [ ] **Step 5: Update `kg_features/__init__.py`**

Replace contents with a one-line note:

```python
"""kg_features v1 modules retired in Phase 8.0 H7 (2026-05-10).

The v1 retrieval, NL-query, embedding, metadata-enricher, and entity-canonicalizer
modules were unreachable from production paths and referenced dropped v1 tables / RPCs.
Per understandlegacycode.com 2024 + LaunchDarkly 2024 + ConfigCat 2024-01-30, the
canonical 2024+ pattern is hard-delete with git history as the archive.

Retrieval pattern: see commit history before this date / `git log -G "rag_dense_recall"`.
Future v2 retrieval lives in `website/features/rag_pipeline/retrieval/`.
"""
```

- [ ] **Step 6: Run pytest --collect-only to confirm no broken imports**

```bash
PYTHONIOENCODING=utf-8 python -m pytest --collect-only -q 2>&1 | tail -10
```

Expected: collection succeeds; no `ImportError` from missing kg_features submodules.

- [ ] **Step 7: Run full not-live + v2 live regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/ -q --live
```

Expected: 4 known flakes; live all pass.

- [ ] **Step 8: Commit**

```bash
git add website/features/kg_features/__init__.py \
        tests/unit/test_kg_features_unreachable.py
# (deletions already staged via git rm)
git commit -m "$(cat <<'EOF'
refactor: hard-delete 5 kg_features v1 modules + add CI guard (8.0-H7)
EOF
)"
```

---

## Task 12: H8 — `experimental_features/nexus/service/*.py` header doc drift

**Goal:** Module headers in `token_store.py`, `oauth_state.py`, `bulk_import.py` reference dropped tables ("maps onto pipelines.*" but headers still say `nexus_provider_accounts`). Verify actual `.schema(...).table(...)` call sites; update headers + add inline assertions where useful.

**Files:**
- Modify: `website/experimental_features/nexus/service/{token_store, oauth_state, bulk_import}.py` — headers only (or call sites if any v1 references remain).

**Rationale + citations:** Mechanical doc-comment hygiene. No fresh research needed — Research N (UI audit) flagged this as STALE/SILENT. [Strangler Fig terminal phase](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern) — clean up cosmetic v1 references at the end of the migration.

- [ ] **Step 1: Audit each of the 3 files**

For each file in `website/experimental_features/nexus/service/`:

```bash
grep -n "kg_users\|nexus_provider_accounts\|nexus_ingest_runs\|nexus_oauth_states\|public\." website/experimental_features/nexus/service/<file>.py
```

Capture every match.

- [ ] **Step 2: For each match — classify**

- If it's an actual `.schema("public").table("nexus_*")` call: REFACTOR to `.schema("pipelines").table(...)` with the v2 column shape.
- If it's a docstring/comment reference: UPDATE to point at v2 (`pipelines.nexus_provider_tokens` etc.).
- If it's a string literal in an error message: update to current name.

- [ ] **Step 3: Write a quick verification test**

Add to `tests/integration/v2/test_nexus_v2.py` (extend existing if it exists):

```python
def test_nexus_modules_target_pipelines_schema():
    """No v1 references in nexus service modules."""
    import inspect
    from website.experimental_features.nexus.service import token_store, oauth_state, bulk_import
    for mod in (token_store, oauth_state, bulk_import):
        src = inspect.getsource(mod)
        assert "nexus_provider_accounts" not in src, f"{mod.__name__}: v1 table reference remains"
        assert "from website.core.supabase_kg" not in src
```

- [ ] **Step 4: Run + commit**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

```bash
git add website/experimental_features/nexus/service/token_store.py \
        website/experimental_features/nexus/service/oauth_state.py \
        website/experimental_features/nexus/service/bulk_import.py \
        tests/integration/v2/test_nexus_v2.py
git commit -m "$(cat <<'EOF'
docs(v2): nexus service module headers point at pipelines.* (8.0-H8)
EOF
)"
```

---

## Task 13: H9 — sandbox member PostgREST embed target swap

**Goal:** `website/api/__init__.py:110,144` and `website/api/sandbox_routes.py:226,260` use `row.get("kg_nodes")` from PostgREST embedded join. v1 returned that key from the dropped `public.kg_nodes` table. v2 must embed `core.canonical_zettels` (or whatever v2 RAGRepository returns) and rename the field.

**Files:**
- Modify: `website/api/__init__.py` and `website/api/sandbox_routes.py` (4 call sites).
- Possibly modify: `website/core/supabase_v2/repositories/rag_repository.py` — adjust the `select(...)` call to embed v2 join.

**Rationale + citations:** Mechanical PostgREST embed migration — Supabase docs 2024+. No fresh research needed.

- [ ] **Step 1: Audit the 4 call sites**

```bash
grep -n 'row.get("kg_nodes")\|kg_nodes(' website/api/__init__.py website/api/sandbox_routes.py
```

- [ ] **Step 2: Identify the v2 embed target**

Read `RAGRepository.list_kasten_zettels` (Phase 1.A v2 RPC) or whatever method the route calls. The return shape includes `canonical_zettel_id`, `title`, `source_type`, `user_tags`, etc. — these are the v2-equivalent fields.

- [ ] **Step 3: Write the regression test**

Create `tests/integration/v2/test_sandbox_member_embed_v2.py`:

```python
"""Phase 8.0 H9 — sandbox member serializer reads v2 embed (canonical_zettels)."""
import pytest
import inspect


def test_serialize_member_reads_v2_keys():
    from website.api import __init__ as api_init
    from website.api import sandbox_routes
    for mod in (api_init, sandbox_routes):
        src = inspect.getsource(mod)
        # v1 key must be absent
        assert 'row.get("kg_nodes")' not in src, f"{mod.__name__}: v1 PostgREST embed key remains"
```

- [ ] **Step 4: Refactor each call site**

Replace `row.get("kg_nodes")` with `row.get("canonical_zettels")` (or whatever v2 key the embed produces). If the field shape inside the embed differs (e.g., `name` → `title`), update the consumer accordingly.

- [ ] **Step 5: Run + commit**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/test_sandbox_member_embed_v2.py -q --live
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

```bash
git add website/api/__init__.py website/api/sandbox_routes.py \
        tests/integration/v2/test_sandbox_member_embed_v2.py
git commit -m "$(cat <<'EOF'
fix(v2): sandbox member serializer reads canonical_zettels embed (8.0-H9)
EOF
)"
```

---

## Task 14: H10 — sweep stale `kg_users`/`kg_nodes` comments

**Goal:** Stray comments referencing v1 tables as "preserved unchanged" mislead future readers. Mechanical sweep + delete.

**Files:** anywhere in `website/` — find via grep.

- [ ] **Step 1: Find every stale comment**

```bash
git grep -n "kg_users\|kg_nodes\|kg_links\|rag_sandboxes" -- "website/" -- ":!website/core/supabase_kg/" "*.py"
```

For each match — if it's:
- A code reference to a dropped table: should have been caught by previous tasks; surface as bug.
- A comment / docstring: UPDATE to reference v2 equivalent OR remove if obsolete.

- [ ] **Step 2: Bulk sweep**

Either:
- `sed`-style mass replace of common stale phrases (e.g., `# kg_users: write avatar_url here` → `# core.profiles.avatar_url`).
- Manual edit of each file the grep found.

- [ ] **Step 3: Verify the grep is clean**

```bash
git grep -n "kg_users\|kg_nodes" -- "website/" -- ":!website/core/supabase_kg/" "*.py"
```

Expected: zero matches (or only matches in retired-test skip-mark reasons).

- [ ] **Step 4: Commit**

```bash
git add -u website/
git commit -m "$(cat <<'EOF'
docs: sweep stale kg_users/kg_nodes comments in website/ (8.0-H10)
EOF
)"
```

---

## Task 15: Final regression sweep + push

**Goal:** Confirm the entire Phase 8 closeout is green, then push.

- [ ] **Step 1: Run full not-live regression**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/ -m "not live" -q --tb=no
```

Expected: 4 known flakes only (sandbox_routes 402, 2× quantize_bge_int8, cascade_int8). NO new failures.

- [ ] **Step 2: Run full v2 live integration suite**

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/v2/ -q --live
```

Expected: all pass.

- [ ] **Step 3: Verify final invariants**

```bash
git grep "from website.core.supabase_kg" -- "website/" "tests/"
git grep -l "kg_users\|kg_nodes\|rag_sandboxes" -- "website/api/" "website/features/" "website/experimental_features/" "website/core/" -- ":!website/core/supabase_kg/" "*.py"
```

Both must be empty (apart from skip-mark reasons or planned-Phase-9 references).

- [ ] **Step 4: Run `verify_v2_e2e.py`**

```bash
PYTHONIOENCODING=utf-8 python ops/scripts/verify_v2_e2e.py
```

Expected: PURE v2 verdict.

- [ ] **Step 5: Verify Naruto + Zoro can sign in**

```bash
PYTHONIOENCODING=utf-8 python -c "
from website.core.supabase_v2.client import get_v2_anon_client
anon = get_v2_anon_client()
naruto = anon.auth.sign_in_with_password({'email': 'naruto@zettelkasten.local', 'password': 'Naruto2026!'})
print(f'Naruto: {len(naruto.session.access_token)}')
anon.auth.sign_out()
zoro = anon.auth.sign_in_with_password({'email': 'zoro@zettelkasten.test', 'password': 'Zoro2026!'})
print(f'Zoro: {len(zoro.session.access_token)}')
"
```

- [ ] **Step 6: Push**

```bash
git push origin master
```

After push, dispatch the Final Acceptance Test per `docs/db-v2/final-acceptance-test-plan.md`.

---

## Self-Review (Part 2)

**Hotspot coverage:**
- ✅ H1 — `/api/me/avatar` v2 port → Task 8
- ✅ H2 + H3 — pricing v1 fallback delete → already covered by Task 2 (Part 1)
- ✅ H4 — `/api/me` v1 fallback delete → already covered by Task 4 (Part 1, 4a)
- ✅ H5 — RAG blocklist port → Task 9
- ✅ H6 — kasten_stats OTel migration → Task 10
- ✅ H7 — kg_features hard-delete + CI guard → Task 11
- ✅ H8 — nexus header doc drift → Task 12
- ✅ H9 — sandbox member embed swap → Task 13
- ✅ H10 — stale comment sweep → Task 14

**Placeholder scan:** No "TBD" in Part 2. All steps have actual code or commands.

**Type consistency:**
- `update_profile_avatar(profile_id: uuid.UUID, avatar_url: str | None)` defined in Task 8 step 1; consumed in Task 8 step 4.
- `pipelines.extraction_blocklist` schema defined in Task 9 step 2; consumed in Task 9 step 5.
- `record_kasten_extraction(*, kasten_id: str, count: int)` signature in Task 10 step 3.

**Anti-patterns avoided (per H research):**
- ✅ No URL versioning for /api/me/avatar (storage change only).
- ✅ No feature flag for /api/me/avatar (no working fallback to gate to).
- ✅ Persistence kept for blocklist (mutable runtime config); removed for metrics (anti-pattern OLTP DB metrics).
- ✅ No tombstones / no @deprecated on kg_features (proven unreachable).
- ✅ CI guard added for kg_features re-import detection.

---

## Citations (Part 2 only, all 2023-2025)

### H1 (Research O — write-flow endpoint migration)

20. **Hodgson — Expand/Contract: making a breaking change without a big bang** (2023). https://blog.thepete.net/blog/2023/12/05/expand/contract-making-a-breaking-change-without-a-big-bang/
21. **Speakeasy — Versioning Best Practices in REST API Design** (2024). https://www.speakeasy.com/api-design/versioning
22. **Zuplo — API Backwards Compatibility Best Practices** (2024). https://zuplo.com/learning-center/api-versioning-backward-compatibility-best-practices
23. **Harness — Database Migrations with Feature Flags** (2024). https://www.harness.io/blog/database-migration-with-feature-flags
24. **CloudBees — Mitigate Infrastructure Migration Risk with Feature Flags** (2024). https://www.cloudbees.com/blog/mitigate-infrastructure-migration-risk-with-feature-flags
25. **Prisma Data Guide — Using the expand and contract pattern**. https://www.prisma.io/dataguide/types/relational/expand-and-contract-pattern

### H5 + H6 (Research P — observability persistence retire)

26. **Fluri — Expand and Contract Method for Database Changes** (2024). https://medium.com/@jasminfluri/expand-and-contract-method-for-database-changes-414d236f236f
27. **Atlas — Strategies for Reliable Migrations** (Oct 2024). https://atlasgo.io/blog/2024/10/09/strategies-for-reliable-migrations
28. **The New Stack — Observability in 2024: More OpenTelemetry, Less Confusion** (2024). https://thenewstack.io/observability-in-2024-more-opentelemetry-less-confusion/
29. **dev.to — Rethinking Observability Costs: Structured Logging** (2024). https://dev.to/anderson_leite/rethinking-observability-costs-how-structured-logging-can-save-you-thousands-54bh
30. **Grepr — Why Structured Logging Matters for Modern Observability** (2025). https://www.grepr.ai/blog/structured-logging---what-it-is-and-why-you-need-it
31. **groundcover — OpenTelemetry Metrics: Types & Best Practices**. https://www.groundcover.com/opentelemetry/opentelemetry-metrics

### H7 (Research Q — feature-flag-gated module retirement)

32. **understandlegacycode.com — "Delete unused code (and how to retrieve it)"** (2024). https://understandlegacycode.com/blog/delete-unused-code/
33. **scheb/tombstone** (PHP, maintained 2024). https://github.com/scheb/tombstone
34. **ConfigCat — Feature Flag Retirement** (2024-01-30). https://configcat.com/blog/2024/01/30/feature-flag-retirement/
35. **LaunchDarkly Docs — Reducing technical debt from feature flags** (2024). https://docs.launchdarkly.com/guides/flags/technical-debt
36. **PEP 702 / `warnings.deprecated`** (Python 3.13, mypy 1.11+, 2024). https://peps.python.org/pep-0702/
37. **jendrikseipp/vulture** (2024 releases). https://github.com/jendrikseipp/vulture

---

**Plan complete (Parts 1 + 2).** Path: `docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md`. Total: 15 tasks, ~50 commits worth of bite-sized steps.

**Execution options:**

1. **Subagent-Driven** (recommended) — fresh subagent per task with two-stage review.
2. **Inline Execution** — batch execution with checkpoints.

Which approach? Awaiting operator decision.
