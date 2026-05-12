"""WAVE-B Phase 1b — `user_zettels` UZ-01 + UZ-02 BOLA tests.

Strategy ref: `docs/research/full_modular_test_plans/user_zettels.md`.
Industry standard: OWASP API1:2023 (Broken Object Level Authorization).

UZ-01: User A cannot enumerate or mutate User B's zettel UUIDs by ID guess.
  - Bulk-insert 20 zettels into A's workspace.
  - User B calls `/api/graph?view=my` and gets ZERO of A's wz_ids back.
  - User B's PATCH / DELETE against A's wz_ids leaves A's rows unchanged
    (verified by direct DB read-back — Supabase silent-200 trap on RLS).
  - UUID-leak canaries (auth_user_id, profile_id, workspace_id, email) must
    not appear anywhere in B's response bodies.

UZ-02: Cross-workspace isolation under shared Kasten membership.
  - A owns workspace WA + kasten KA; B owns workspace WB.
  - A shares KA with WB (B becomes Kasten member, gains SELECT on kasten
    contents only).
  - B's `/api/graph?view=my` MUST still only enumerate B's own workspace
    zettels — sharing a Kasten does NOT broaden the personal-graph scope.
  - B's PATCH / DELETE against A's workspace_zettel rows MUST be rejected
    even when those rows are visible to B through the shared Kasten
    (read access != write access).
"""
from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# App fixture (mirrors test_cross_tenant_denial.v2_app)
# ---------------------------------------------------------------------------
@pytest.fixture
def v2_app(monkeypatch):
    """FastAPI app with DB v2 forced; stub Gemini/Anthropic keys so the RAG
    runtime constructs cleanly. Cross-tenant zettel surface rejects at the
    RLS / workspace-scope layer BEFORE any LLM call, so no live API hits.
    """
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-cross-tenant-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-cross-tenant-tests")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.app import create_app
    return create_app()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _uuid_leak_assert(resp_text: str, victim, label: str) -> None:
    """Standard UUID-leak canary assertion (OWASP API1:2023 BOLA pattern).

    A successful BOLA exploit usually surfaces as a victim UUID / email
    appearing in the attacker's response — assert none of them do. Mirrors
    the assertions in test_cross_tenant_denial.py.
    """
    assert victim.email not in resp_text, f"[{label}] victim email leaked"
    assert str(victim.auth_user_id) not in resp_text, (
        f"[{label}] cross-tenant leak: victim auth_user_id in response"
    )
    assert str(victim.profile_id) not in resp_text, (
        f"[{label}] cross-tenant leak: victim profile_id in response"
    )
    for ws_id in victim.workspace_ids:
        assert str(ws_id) not in resp_text, (
            f"[{label}] cross-tenant leak: victim workspace_id {ws_id} in response"
        )


# ---------------------------------------------------------------------------
# UZ-01 — stream-load BOLA (user A cannot see/mutate user B's wz_ids)
# ---------------------------------------------------------------------------
def test_uz01_stream_load_no_cross_tenant_enumeration(
    v2_app, mint_user, bulk_insert_zettels, asyncpg_pool,
):
    """User B's `/api/graph?view=my` MUST NOT contain any of user A's wz_ids
    or canonical zettel UUIDs. Seeds N=20 zettels in A's workspace.
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)

    # Seed 20 zettels in A's personal workspace.
    a_wz_ids = asyncio.get_event_loop().run_until_complete(
        bulk_insert_zettels(owner_user=a, n=20, prefix="uz01-a")
    )
    assert len(a_wz_ids) == 20

    with TestClient(v2_app) as client:
        # B requests their own personal graph view.
        resp = client.get("/api/graph?view=my&limit=5000", headers=_auth(b.jwt))

    assert resp.status_code == 200, resp.text
    body_text = resp.text

    # None of A's workspace_zettel UUIDs may appear (raw UUID or substring of
    # the slugged node_id — node_id embeds `canonical_id[:8]` per
    # routes._v2_assemble_graph).
    for wz_id in a_wz_ids:
        assert str(wz_id) not in body_text, (
            f"A's workspace_zettel_id {wz_id} leaked into B's /api/graph?view=my"
        )

    # Also assert canonical UUIDs do not leak. Query A's canonical IDs and
    # check each is absent from B's response.
    async def _fetch_canonical_ids():
        async with asyncpg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT canonical_zettel_id FROM content.workspace_zettels "
                "WHERE id = ANY($1::uuid[])",
                a_wz_ids,
            )
            return [str(r["canonical_zettel_id"]) for r in rows]

    a_canonical_ids = asyncio.get_event_loop().run_until_complete(
        _fetch_canonical_ids()
    )
    for cz_id in a_canonical_ids:
        assert cz_id not in body_text, (
            f"A's canonical_zettel_id {cz_id} leaked into B's /api/graph?view=my"
        )

    # Standard UUID-leak canaries.
    _uuid_leak_assert(body_text, victim=a, label="uz01-stream")


def test_uz01_patch_by_uuid_guess_does_not_modify(
    v2_app, mint_user, bulk_insert_zettels, asyncpg_pool,
):
    """Under direct UUID guess (PATCH /api/zettels/{a_wz_id} by B), A's row
    MUST be unchanged. Mirrors test_cross_tenant_denial.test_zettel_patch_*
    but at scale across 20 zettels to confirm no compound-key boundary slip.
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    a_wz_ids = asyncio.get_event_loop().run_until_complete(
        bulk_insert_zettels(owner_user=a, n=20, prefix="uz01p-a")
    )

    # B PATCHes each of A's wz_ids attempting to overwrite user_note.
    poisoned = f"poisoned-by-B-{uuid.uuid4().hex[:8]}"
    with TestClient(v2_app) as client:
        for wz_id in a_wz_ids:
            resp = client.patch(
                f"/api/zettels/{wz_id}",
                headers=_auth(b.jwt),
                json={"user_note": poisoned},
            )
            # Any non-2xx is acceptable denial; 200 with row unchanged also
            # acceptable (Supabase silent-200 trap — we verify via DB).
            assert resp.status_code in (200, 400, 403, 404, 500), resp.text
            # UUID-leak canary on every response.
            _uuid_leak_assert(resp.text, victim=a, label=f"uz01-patch-{wz_id}")

    # Read back A's rows; none must carry the poisoned value.
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, user_note FROM content.workspace_zettels "
                "WHERE id = ANY($1::uuid[])",
                a_wz_ids,
            )
            return [(str(r["id"]), r["user_note"]) for r in rows]

    after = asyncio.get_event_loop().run_until_complete(_check())
    poisoned_hits = [(wid, note) for wid, note in after if note == poisoned]
    assert poisoned_hits == [], (
        f"User B's PATCH compromised {len(poisoned_hits)} of A's zettels: "
        f"{poisoned_hits[:3]}"
    )


def test_uz01_delete_by_uuid_guess_does_not_remove(
    v2_app, mint_user, bulk_insert_zettels, asyncpg_pool,
):
    """B's DELETE against each of A's wz_ids MUST NOT remove or soft-delete
    A's rows. Verified via direct DB read-back (deleted_at IS NULL on all).
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    a_wz_ids = asyncio.get_event_loop().run_until_complete(
        bulk_insert_zettels(owner_user=a, n=20, prefix="uz01d-a")
    )

    with TestClient(v2_app) as client:
        for wz_id in a_wz_ids:
            resp = client.delete(
                f"/api/zettels/{wz_id}", headers=_auth(b.jwt),
            )
            # Acceptable: explicit denial OR silent-200 (verified via DB).
            assert resp.status_code in (200, 400, 403, 404, 500), resp.text
            _uuid_leak_assert(resp.text, victim=a, label=f"uz01-del-{wz_id}")

    async def _check():
        async with asyncpg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, deleted_at FROM content.workspace_zettels "
                "WHERE id = ANY($1::uuid[])",
                a_wz_ids,
            )
            return [(str(r["id"]), r["deleted_at"]) for r in rows]

    after = asyncio.get_event_loop().run_until_complete(_check())
    missing = [wid for wid in a_wz_ids if str(wid) not in {r[0] for r in after}]
    deleted = [(wid, dt) for wid, dt in after if dt is not None]
    assert not missing, f"User B's DELETE hard-removed A's zettels: {missing[:3]}"
    assert not deleted, (
        f"User B's DELETE soft-deleted A's zettels: {deleted[:3]}"
    )


# ---------------------------------------------------------------------------
# UZ-02 — cross-workspace isolation under shared Kasten membership
# ---------------------------------------------------------------------------
async def _add_zettel_to_kasten(
    pool: asyncpg.Pool, *, kasten_id: uuid.UUID, wz_ids: list[uuid.UUID],
) -> None:
    """Add workspace zettels to a kasten via direct service-role insert
    (bypasses RLS for fixture setup). Mirrors test_kasten_share_e2e patterns.
    """
    # added_via NOT NULL + CHECK constraint per _v2/04_rag_schema.sql:32;
    # 'manual' is always-valid (the base set, before 13_v2_kasten_rpcs added 'bulk_rpc').
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via) "
            "VALUES ($1, $2, 'manual') ON CONFLICT DO NOTHING",
            [(kasten_id, wz) for wz in wz_ids],
        )


async def _add_kasten_member(
    pool: asyncpg.Pool, *, kasten_id: uuid.UUID, workspace_id: uuid.UUID,
    role: str = "viewer",
) -> None:
    """Insert a kasten_members row directly (service-role bypass)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.kasten_members (kasten_id, workspace_id, role) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            kasten_id, workspace_id, role,
        )


def test_uz02_shared_kasten_does_not_widen_personal_graph(
    v2_app, mint_user, mint_kasten, bulk_insert_zettels, asyncpg_pool,
):
    """A shares Kasten KA (containing A's zettels) with B's workspace.
    B's `/api/graph?view=my` MUST still only return B's OWN-workspace zettels.
    A shared Kasten grants read access to Kasten contents, NOT a widened
    personal-graph scope.
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    ka = mint_kasten(owner_user=a, name=f"uz02-ka-{uuid.uuid4().hex[:6]}")

    # Seed 20 zettels for A and 20 for B.
    a_wz_ids = asyncio.get_event_loop().run_until_complete(
        bulk_insert_zettels(owner_user=a, n=20, prefix="uz02-a")
    )
    b_wz_ids = asyncio.get_event_loop().run_until_complete(
        bulk_insert_zettels(owner_user=b, n=20, prefix="uz02-b")
    )

    # Add A's zettels to A's Kasten, then share Kasten with B's workspace.
    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        _add_zettel_to_kasten(asyncpg_pool, kasten_id=ka.sandbox_id, wz_ids=a_wz_ids)
    )
    loop.run_until_complete(
        _add_kasten_member(
            asyncpg_pool,
            kasten_id=ka.sandbox_id,
            workspace_id=b.workspace_ids[0],
            role="viewer",
        )
    )

    # B's personal graph view: must contain B's own zettels but NOT enumerate
    # A's wz_ids as if they were B's. The personal graph is scoped to B's
    # workspace memberships, NOT to kasten memberships.
    with TestClient(v2_app) as client:
        resp = client.get("/api/graph?view=my&limit=5000", headers=_auth(b.jwt))
    assert resp.status_code == 200, resp.text
    body_text = resp.text

    for wz_id in a_wz_ids:
        assert str(wz_id) not in body_text, (
            f"A's wz_id {wz_id} bled into B's personal graph via shared Kasten — "
            f"BOLA: shared-Kasten membership widened personal-graph scope."
        )
    _uuid_leak_assert(body_text, victim=a, label="uz02-graph")


def test_uz02_shared_kasten_grants_read_not_write(
    v2_app, mint_user, mint_kasten, bulk_insert_zettels, asyncpg_pool,
):
    """Even when B can READ A's zettels through a shared Kasten, B's
    PATCH / DELETE against A's wz_ids MUST be denied (compound-key gate on
    workspace_id at content_repo level — see routes.py:475-478, 542-546).
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    ka = mint_kasten(owner_user=a, name=f"uz02w-ka-{uuid.uuid4().hex[:6]}")

    a_wz_ids = asyncio.get_event_loop().run_until_complete(
        bulk_insert_zettels(owner_user=a, n=20, prefix="uz02w-a")
    )

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        _add_zettel_to_kasten(asyncpg_pool, kasten_id=ka.sandbox_id, wz_ids=a_wz_ids)
    )
    loop.run_until_complete(
        _add_kasten_member(
            asyncpg_pool,
            kasten_id=ka.sandbox_id,
            workspace_id=b.workspace_ids[0],
            role="viewer",
        )
    )

    poisoned = f"shared-kasten-poison-{uuid.uuid4().hex[:8]}"
    with TestClient(v2_app) as client:
        # PATCH attempts on every shared wz_id.
        for wz_id in a_wz_ids:
            resp = client.patch(
                f"/api/zettels/{wz_id}",
                headers=_auth(b.jwt),
                json={"user_note": poisoned},
            )
            assert resp.status_code in (200, 400, 403, 404, 500), resp.text
            _uuid_leak_assert(resp.text, victim=a, label=f"uz02-patch-{wz_id}")

        # DELETE attempts on every shared wz_id.
        for wz_id in a_wz_ids:
            resp = client.delete(
                f"/api/zettels/{wz_id}", headers=_auth(b.jwt),
            )
            assert resp.status_code in (200, 400, 403, 404, 500), resp.text
            _uuid_leak_assert(resp.text, victim=a, label=f"uz02-del-{wz_id}")

    # Verify A's rows are untouched.
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, user_note, deleted_at "
                "FROM content.workspace_zettels WHERE id = ANY($1::uuid[])",
                a_wz_ids,
            )
            return [(str(r["id"]), r["user_note"], r["deleted_at"]) for r in rows]

    after = asyncio.get_event_loop().run_until_complete(_check())
    poisoned_hits = [(wid, note) for wid, note, _ in after if note == poisoned]
    deleted = [(wid, dt) for wid, _, dt in after if dt is not None]
    assert not poisoned_hits, (
        f"Shared-Kasten read access leaked into write access — B's PATCH "
        f"compromised {len(poisoned_hits)} of A's zettels: {poisoned_hits[:3]}"
    )
    assert not deleted, (
        f"Shared-Kasten read access leaked into delete access — B's DELETE "
        f"soft-deleted {len(deleted)} of A's zettels: {deleted[:3]}"
    )
    # All 20 must still be present (read-back size matches).
    assert len(after) == len(a_wz_ids), (
        f"Some of A's zettels are missing entirely: got {len(after)} of "
        f"{len(a_wz_ids)} — hard-delete on a shared row?"
    )
