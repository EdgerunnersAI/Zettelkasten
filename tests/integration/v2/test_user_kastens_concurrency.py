"""WAVE-B Phase 1b — UK-02: concurrent add/remove member idempotency.

Spec: ``docs/research/full_modular_test_plans/user_kastens.md``.

Double-click / double-submit on the share + member-add buttons must be safe.
The underlying invariants we check:

  1. Two parallel ``POST /api/rag/sandboxes/{id}/share`` calls with the same
     ``(kasten_id, recipient_workspace_id, role)`` payload must leave exactly
     ONE ``rag.kasten_members`` row — the v2 trigger / unique index must collapse
     the second insert.

  2. Two parallel ``POST /api/rag/sandboxes/{id}/members`` calls with the same
     ``node_ids`` payload (the v2 bulk-add path) must end with the zettel
     present exactly once in ``rag.kasten_zettels`` — the v2 RPC must be
     idempotent on workspace_zettel_id.

  3. Two parallel ``DELETE /api/rag/sandboxes/{id}/members/{node_id}`` calls on
     the same row must both succeed at most once at the DB level: the row is
     gone after, neither call raised an unhandled 500. (One returns 200, the
     other returns 200 or 404 depending on race timing — both are acceptable
     idempotency surfaces.)

Concurrency is exercised via ``ThreadPoolExecutor`` because ``TestClient`` is
synchronous; two threads issue the same request near-simultaneously and the
test asserts the DB-level invariant after both return.
"""
from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# v2 app fixture (parallel to test_user_kastens_bola.v2_app)
# ---------------------------------------------------------------------------


@pytest.fixture
def v2_app(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None

    async def _noop(*_args, **_kwargs):  # noqa: D401
        return None

    from website.api import sandbox_routes as sandbox_routes_mod
    monkeypatch.setattr(sandbox_routes_mod, "require_entitlement", _noop)
    monkeypatch.setattr(sandbox_routes_mod, "consume_entitlement", _noop)

    from website.app import create_app

    return create_app()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _seed_workspace_zettel(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID
) -> uuid.UUID:
    cz_id = uuid.uuid4()
    wz_id = uuid.uuid4()
    norm_url = f"https://uk-conc-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, 'web', $4, 'body', '2026-04-01'::date)
            """,
            cz_id, norm_url, chash, "uk concurrency fixture",
        )
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, ai_summary,
                 user_tags, user_note, pinned, added_via)
            VALUES ($1, $2, $3, $4, $5, NULL, false, 'website')
            """,
            wz_id, workspace_id, cz_id,
            '{"brief_summary": "uk conc", "detailed_summary": "uk conc detail"}',
            ["uk-conc"],
        )
    return wz_id


# ---------------------------------------------------------------------------
# UK-02a — Concurrent /share (double-click) is idempotent at the DB layer
# ---------------------------------------------------------------------------


def test_uk02_concurrent_share_is_idempotent(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Two parallel POST /share calls with the same payload land exactly one
    rag.kasten_members row (besides the auto-owner row).

    The auto-owner trigger inserts (owner_workspace, owner) at kasten create,
    so after a successful share with the recipient we expect exactly TWO rows
    total — the owner row + the viewer row — regardless of how many times the
    /share call ran.
    """
    owner = mint_user(workspace_count=1)
    recipient = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)
    recipient_ws = recipient.workspace_ids[0]

    payload = {"workspace_id": str(recipient_ws), "role": "viewer"}

    def _share() -> int:
        # Each thread owns its own TestClient — TestClient is not thread-safe
        # for concurrent calls against a single instance, but the underlying
        # ASGI app is. Two independent TestClient(app) wrappers around the
        # same app object exercise the route in parallel cleanly.
        with TestClient(v2_app) as client:
            r = client.post(
                f"/api/rag/sandboxes/{kasten.sandbox_id}/share",
                json=payload,
                headers=_auth(owner.jwt),
            )
            return r.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _share(), range(2)))

    # Both calls must complete cleanly. The valid surface is:
    #   - 200/200: trigger's INSERT ... ON CONFLICT collapses the second row
    #   - 200/409: route surfaces the unique-violation as 409
    #   - 200/500 is NOT acceptable — that means an unhandled IntegrityError leaked
    assert all(s in (200, 409) for s in results), (
        f"concurrent /share returned an unexpected status: {results}"
    )
    assert 200 in results, (
        f"at least one /share call must succeed (200), got {results}"
    )

    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT workspace_id, role FROM rag.kasten_members
                 WHERE kasten_id = $1
                """,
                kasten.sandbox_id,
            )

    rows = asyncio.get_event_loop().run_until_complete(_check())
    # Expect: owner row + recipient row = 2 rows.
    assert len(rows) == 2, (
        f"expected exactly 2 kasten_members rows (owner + viewer), got "
        f"{len(rows)}: {[dict(r) for r in rows]}"
    )
    recipient_rows = [r for r in rows if r["workspace_id"] == recipient_ws]
    assert len(recipient_rows) == 1, (
        f"recipient workspace must appear exactly once, got "
        f"{len(recipient_rows)}: {[dict(r) for r in recipient_rows]}"
    )
    assert recipient_rows[0]["role"] == "viewer"


# ---------------------------------------------------------------------------
# UK-02b — Concurrent bulk-add (double-submit) is idempotent
# ---------------------------------------------------------------------------


def test_uk02_concurrent_bulk_add_member_is_idempotent(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Two parallel POST /members calls with the same node_ids payload result
    in the zettel being present exactly once in rag.kasten_zettels."""
    owner = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)
    ws = owner.workspace_ids[0]

    wz = asyncio.get_event_loop().run_until_complete(
        _seed_workspace_zettel(asyncpg_pool, workspace_id=ws)
    )

    payload = {"node_ids": [str(wz)], "added_via": "manual"}

    def _add() -> int:
        with TestClient(v2_app) as client:
            r = client.post(
                f"/api/rag/sandboxes/{kasten.sandbox_id}/members",
                json=payload,
                headers=_auth(owner.jwt),
            )
            return r.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _add(), range(2)))

    # Both calls go through the v2 bulk-add RPC (sandbox_routes.py:579-603),
    # which inserts into rag.kasten_zettels. The RPC uses
    # ON CONFLICT (kasten_id, workspace_zettel_id) DO NOTHING (per
    # 2026-04-26_fix_rag_bulk_add_to_sandbox.sql lineage), so both calls
    # must return 200 — one performs the insert, the other no-ops.
    assert all(s == 200 for s in results), (
        f"concurrent bulk-add must both return 200, got {results}"
    )

    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT count(*) FROM rag.kasten_zettels
                 WHERE kasten_id = $1 AND workspace_zettel_id = $2
                """,
                kasten.sandbox_id, wz,
            )

    count = asyncio.get_event_loop().run_until_complete(_check())
    assert count == 1, (
        f"zettel must appear exactly once in kasten_zettels, got {count} rows"
    )


# ---------------------------------------------------------------------------
# UK-02c — Concurrent remove member is idempotent (no spurious 500s)
# ---------------------------------------------------------------------------


def test_uk02_concurrent_remove_member_is_idempotent(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Two parallel DELETE /members/{node_id} calls on the same row both
    return cleanly (one 200, the other 200 or 404 by race), the row is gone,
    and no unhandled exception escapes."""
    owner = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)
    ws = owner.workspace_ids[0]

    wz = asyncio.get_event_loop().run_until_complete(
        _seed_workspace_zettel(asyncpg_pool, workspace_id=ws)
    )

    # Seed the membership so there's something to delete twice.
    with TestClient(v2_app) as client:
        r = client.post(
            f"/api/rag/sandboxes/{kasten.sandbox_id}/members",
            json={"node_ids": [str(wz)], "added_via": "manual"},
            headers=_auth(owner.jwt),
        )
        assert r.status_code == 200, r.text[:400]

    def _remove() -> int:
        with TestClient(v2_app) as client:
            r = client.delete(
                f"/api/rag/sandboxes/{kasten.sandbox_id}/members/{wz}",
                headers=_auth(owner.jwt),
            )
            return r.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _remove(), range(2)))

    # The DELETE handler routes through the v1 runtime
    # (sandbox_routes.py:637-647) — there is no v2 branch wired today. If the
    # v1 runtime is unavailable in the test env it surfaces as 503; in that
    # case skip rather than fail. Otherwise we require clean idempotency:
    # one 200 + (200 or 404).
    if all(s == 503 for s in results):
        pytest.skip("Sandbox runtime unavailable for DELETE member (v1 path)")

    # Filter out 503 only if a mix happened — we still expect at least one
    # 200, no 500s.
    assert 500 not in results, (
        f"concurrent DELETE leaked a 500 (unhandled): {results}"
    )
    assert any(s == 200 for s in results), (
        f"at least one concurrent DELETE must succeed, got {results}"
    )
    for s in results:
        assert s in (200, 404, 503), (
            f"DELETE returned unexpected status {s} (results={results})"
        )

    # Row must be gone regardless of race ordering.
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT count(*) FROM rag.kasten_zettels
                 WHERE kasten_id = $1 AND workspace_zettel_id = $2
                """,
                kasten.sandbox_id, wz,
            )

    count = asyncio.get_event_loop().run_until_complete(_check())
    # If both DELETE calls returned 503 (runtime unavailable) the row will
    # remain — we already skipped above for that case. With at least one 200,
    # the row must be gone.
    assert count == 0, (
        f"row must be removed after concurrent DELETEs; found {count} rows "
        f"(results={results})"
    )
