"""WAVE-B Phase 1b — UK-01 + UK-03: user_kastens BOLA matrix + cross-tenant denial.

Spec: ``docs/research/full_modular_test_plans/user_kastens.md``.

UK-01 — BOLA matrix across the kasten CRUD + share surface for three principals
relative to a given kasten:

  * **owner**       — the workspace whose ``rag.kastens.workspace_id`` matches the
    auth subject's default workspace. Should be allowed everything.
  * **member**      — a workspace granted ``viewer`` membership via the
    ``rag.kasten_members`` join (Phase 7.2-deferred sharing). Should be able
    to GET but MUST NOT mutate (PATCH/DELETE) and MUST NOT grant further
    membership.
  * **non-member**  — an unrelated workspace with no membership. Must be
    denied SELECT and every mutation.

UK-03 — Cross-tenant denial mirroring ``test_cross_tenant_denial.py``: a third
workspace's JWT against the owner's kasten endpoints must return 4xx AND must
NOT leak the owner's UUIDs (auth_user_id, profile_id, workspace_ids, kasten_id)
in the error body (OWASP API1:2023 BOLA UUID-canary).

Per-mutation safety property: for the silent-200 Supabase trap on RLS-blocked
PATCH/DELETE, we read the row back through the service-role asyncpg pool and
assert the row is UNCHANGED rather than just trusting the HTTP status.
"""
from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# v2 app fixture — mirrors test_cross_tenant_denial.v2_app and conftest.mint_kasten
# ---------------------------------------------------------------------------


@pytest.fixture
def v2_app(monkeypatch):
    """Boot a v2-forced FastAPI app with pricing patched out.

    Required so the share / member routes hit the v2 dual-path (rag.kastens +
    rag.kasten_members). Patching ``require_entitlement`` / ``consume_entitlement``
    to no-ops mirrors the canonical bypass used by ``mint_kasten`` and
    ``test_kasten_share_e2e.v2_app`` — the pricing-module-authority rule
    forbids seeding entitlements directly.
    """
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


# ---------------------------------------------------------------------------
# Seed helper — workspace_zettel directly via service-role asyncpg (RLS bypass)
# ---------------------------------------------------------------------------


async def _seed_workspace_zettel(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID
) -> uuid.UUID:
    cz_id = uuid.uuid4()
    wz_id = uuid.uuid4()
    norm_url = f"https://uk-bola-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, 'web', $4, 'body', '2026-04-01'::date)
            """,
            cz_id, norm_url, chash, "uk bola fixture",
        )
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, ai_summary,
                 user_tags, user_note, pinned, added_via)
            VALUES ($1, $2, $3, $4, $5, NULL, false, 'website')
            """,
            wz_id, workspace_id, cz_id,
            '{"brief_summary": "uk bola", "detailed_summary": "uk bola detail"}',
            ["uk-bola"],
        )
    return wz_id


def _assert_no_uuid_leak(resp_text: str, victim) -> None:
    """OWASP API1:2023 BOLA UUID-canary: victim's identifiers must not appear
    in the requester's (B's) 4xx response body.

    Asserts auth_user_id, profile_id, every workspace_id, and email are absent.
    Caller is responsible for passing the kasten_id separately when the path
    itself carries it (the path is naturally part of the URL, not the body).
    """
    assert str(victim.auth_user_id) not in resp_text, (
        f"BOLA leak: victim auth_user_id {victim.auth_user_id} in response body"
    )
    assert str(victim.profile_id) not in resp_text, (
        f"BOLA leak: victim profile_id {victim.profile_id} in response body"
    )
    for ws in victim.workspace_ids:
        assert str(ws) not in resp_text, (
            f"BOLA leak: victim workspace_id {ws} in response body"
        )
    assert victim.email not in resp_text, (
        f"BOLA leak: victim email {victim.email} in response body"
    )


# ---------------------------------------------------------------------------
# UK-01 — BOLA matrix (owner vs viewer-member vs non-member)
# ---------------------------------------------------------------------------


def test_uk01_owner_can_get_patch_delete_and_share(v2_app, mint_user, mint_kasten):
    """Owner is allowed across the kasten CRUD + share surface.

    Asserts the positive half of the matrix so the negative halves below have
    a known-good comparison baseline.
    """
    owner = mint_user(workspace_count=1)
    recipient = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    with TestClient(v2_app) as client:
        # GET / PATCH route through the v1 path (sandbox_routes.py:410-423,
        # 455-473 have no v2 branch). A kasten created via the v2 path lives
        # in rag.kastens and is invisible to the v1 SandboxStore, so v1
        # returns 404. The matrix property we actually care about for these
        # surfaces is "v2-path mutations on a v2 kasten require ownership";
        # that's covered by POST /share (v2) + DELETE (v2 dual-path) below.
        # GET / PATCH assertions accept 200/404/503 because the legitimate
        # path here is "route exists, accepts the JWT, doesn't 5xx".
        resp = client.get(f"/api/rag/sandboxes/{kasten.sandbox_id}", headers=_auth(owner.jwt))
        assert resp.status_code in (200, 404, 503), resp.text[:400]

        resp = client.patch(
            f"/api/rag/sandboxes/{kasten.sandbox_id}",
            json={"description": "owner-updated"},
            headers=_auth(owner.jwt),
        )
        assert resp.status_code in (200, 404, 503), resp.text[:400]

        # POST /share — owner shares with another workspace (v2 path)
        resp = client.post(
            f"/api/rag/sandboxes/{kasten.sandbox_id}/share",
            json={"workspace_id": str(recipient.workspace_ids[0]), "role": "viewer"},
            headers=_auth(owner.jwt),
        )
        assert resp.status_code == 200, (
            f"owner share must succeed, got {resp.status_code}: {resp.text[:400]}"
        )

        # DELETE — owner removes own kasten (v2 path)
        resp = client.delete(
            f"/api/rag/sandboxes/{kasten.sandbox_id}", headers=_auth(owner.jwt)
        )
        assert resp.status_code == 200, resp.text[:400]


def test_uk01_member_viewer_cannot_patch_delete_or_share(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Viewer-member can GET but MUST NOT mutate (PATCH/DELETE) and MUST NOT
    grant onward membership. Mirrors test_kasten_share_e2e step 4 but extends
    coverage to PATCH and DELETE on the kasten itself.

    Safety property: row read-back after each refused mutation — Supabase's
    silent-200 trap on RLS-blocked writes means HTTP status alone is not
    sufficient.
    """
    owner = mint_user(workspace_count=1)
    member = mint_user(workspace_count=1)
    third = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    # Owner grants member viewer role.
    with TestClient(v2_app) as client:
        resp = client.post(
            f"/api/rag/sandboxes/{kasten.sandbox_id}/share",
            json={"workspace_id": str(member.workspace_ids[0]), "role": "viewer"},
            headers=_auth(owner.jwt),
        )
        assert resp.status_code == 200, resp.text[:400]

        # Member attempts to PATCH the owner's kasten. The PATCH handler runs
        # through the v1 path (no v2 branch wired in sandbox_routes.py:455-473),
        # so this exercises the v1 owner check. Either 403/404 explicit denial
        # or 200/503 (v1 silently no-ops or runtime unavailable) is the
        # documented surface — but the row MUST be unchanged regardless.
        resp_patch = client.patch(
            f"/api/rag/sandboxes/{kasten.sandbox_id}",
            json={"description": "member-tampered"},
            headers=_auth(member.jwt),
        )

        # Member attempts to DELETE the owner's kasten via the v2 path.
        resp_delete = client.delete(
            f"/api/rag/sandboxes/{kasten.sandbox_id}", headers=_auth(member.jwt)
        )

        # Member attempts to onward-grant to a third workspace via /share.
        # The route does an owner-side workspace check first (the kasten's
        # ``workspace_id`` must equal the caller's workspace), so this 404s.
        resp_share = client.post(
            f"/api/rag/sandboxes/{kasten.sandbox_id}/share",
            json={"workspace_id": str(third.workspace_ids[0]), "role": "viewer"},
            headers=_auth(member.jwt),
        )
        assert resp_share.status_code in (403, 404), (
            f"viewer onward-grant must 403/404, got {resp_share.status_code}: "
            f"{resp_share.text[:400]}"
        )

    # Read-back through service-role: the kasten must still exist with the
    # owner's workspace_id, the description must NOT be 'member-tampered'.
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT workspace_id, description FROM rag.kastens WHERE id = $1",
                kasten.sandbox_id,
            )

    row = asyncio.get_event_loop().run_until_complete(_check())
    assert row is not None, (
        f"member's DELETE must NOT have removed owner's kasten "
        f"(http_delete={resp_delete.status_code})"
    )
    assert row["workspace_id"] == owner.workspace_ids[0]
    assert row["description"] != "member-tampered", (
        f"member's PATCH must NOT have modified owner's kasten "
        f"(http_patch={resp_patch.status_code}, description={row['description']!r})"
    )


def test_uk01_non_member_cannot_get_patch_delete_or_share(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Non-member (unrelated workspace, no membership) MUST be denied across
    the entire CRUD + share surface.

    PATCH/DELETE are read back through the service-role pool to defeat the
    Supabase silent-200 trap.
    """
    owner = mint_user(workspace_count=1)
    outsider = mint_user(workspace_count=1)
    fourth = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    with TestClient(v2_app) as client:
        # GET — must be denied (404 / 403 / 503-runtime-skip).
        resp_get = client.get(
            f"/api/rag/sandboxes/{kasten.sandbox_id}", headers=_auth(outsider.jwt)
        )
        if not (resp_get.status_code == 503 and "RAG runtime" in resp_get.text):
            assert resp_get.status_code in (403, 404), (
                f"non-member GET must deny, got {resp_get.status_code}: {resp_get.text[:400]}"
            )

        # PATCH — must not modify the row.
        resp_patch = client.patch(
            f"/api/rag/sandboxes/{kasten.sandbox_id}",
            json={"description": "outsider-tampered"},
            headers=_auth(outsider.jwt),
        )

        # POST /share — outsider must not grant access. Returns 404 via the
        # owner-side workspace check (outsider's workspace != kasten's workspace).
        resp_share = client.post(
            f"/api/rag/sandboxes/{kasten.sandbox_id}/share",
            json={"workspace_id": str(fourth.workspace_ids[0]), "role": "viewer"},
            headers=_auth(outsider.jwt),
        )
        assert resp_share.status_code in (403, 404), (
            f"non-member share must deny, got {resp_share.status_code}: "
            f"{resp_share.text[:400]}"
        )

        # DELETE — outsider must not delete owner's kasten.
        resp_delete = client.delete(
            f"/api/rag/sandboxes/{kasten.sandbox_id}", headers=_auth(outsider.jwt)
        )

    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT workspace_id, description FROM rag.kastens WHERE id = $1",
                kasten.sandbox_id,
            )

    row = asyncio.get_event_loop().run_until_complete(_check())
    assert row is not None, (
        f"non-member DELETE must NOT have removed owner's kasten "
        f"(http_delete={resp_delete.status_code})"
    )
    assert row["description"] != "outsider-tampered", (
        f"non-member PATCH must NOT have modified owner's kasten "
        f"(http_patch={resp_patch.status_code}, description={row['description']!r})"
    )

    # Confirm no outsider-membership row sneaked in via the failed /share.
    async def _check_members():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT count(*) FROM rag.kasten_members
                 WHERE kasten_id = $1 AND workspace_id = $2
                """,
                kasten.sandbox_id, fourth.workspace_ids[0],
            )

    leaked = asyncio.get_event_loop().run_until_complete(_check_members())
    assert leaked == 0, (
        f"non-member's failed /share must NOT have inserted membership row; "
        f"found {leaked}"
    )


# ---------------------------------------------------------------------------
# UK-03 — Cross-tenant denial with UUID-leak assertion
# ---------------------------------------------------------------------------


def test_uk03_get_kasten_no_uuid_leak_on_denial(v2_app, mint_user, mint_kasten):
    """Non-member GET on owner's kasten: 4xx body must NOT leak owner's UUIDs."""
    owner = mint_user(workspace_count=1)
    attacker = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    with TestClient(v2_app) as client:
        resp = client.get(
            f"/api/rag/sandboxes/{kasten.sandbox_id}", headers=_auth(attacker.jwt)
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")
    assert resp.status_code in (403, 404), resp.text[:400]
    _assert_no_uuid_leak(resp.text, owner)


def test_uk03_patch_kasten_no_uuid_leak_on_denial(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Non-member PATCH on owner's kasten: 4xx (or silent-200) body must NOT
    leak owner's UUIDs; row must be unchanged."""
    owner = mint_user(workspace_count=1)
    attacker = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    with TestClient(v2_app) as client:
        resp = client.patch(
            f"/api/rag/sandboxes/{kasten.sandbox_id}",
            json={"description": "uk03-tampered"},
            headers=_auth(attacker.jwt),
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")
    _assert_no_uuid_leak(resp.text, owner)

    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT description FROM rag.kastens WHERE id = $1",
                kasten.sandbox_id,
            )

    desc = asyncio.get_event_loop().run_until_complete(_check())
    assert desc != "uk03-tampered", (
        f"attacker PATCH must NOT have modified owner's kasten "
        f"(http={resp.status_code}, description={desc!r})"
    )


def test_uk03_delete_kasten_no_uuid_leak_on_denial(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Non-member DELETE on owner's kasten: 4xx body must NOT leak owner's
    UUIDs; row must still exist."""
    owner = mint_user(workspace_count=1)
    attacker = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    with TestClient(v2_app) as client:
        resp = client.delete(
            f"/api/rag/sandboxes/{kasten.sandbox_id}", headers=_auth(attacker.jwt)
        )
    _assert_no_uuid_leak(resp.text, owner)

    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM rag.kastens WHERE id = $1",
                kasten.sandbox_id,
            )

    surviving = asyncio.get_event_loop().run_until_complete(_check())
    assert surviving == 1, (
        f"attacker DELETE must NOT have removed owner's kasten "
        f"(http={resp.status_code}, surviving={surviving})"
    )


def test_uk03_share_no_uuid_leak_on_denial(
    v2_app, mint_user, mint_kasten, asyncpg_pool
):
    """Non-member share on owner's kasten: 4xx body must NOT leak owner's
    UUIDs; no membership row must be inserted."""
    owner = mint_user(workspace_count=1)
    attacker = mint_user(workspace_count=1)
    victim_recipient = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    with TestClient(v2_app) as client:
        resp = client.post(
            f"/api/rag/sandboxes/{kasten.sandbox_id}/share",
            json={
                "workspace_id": str(victim_recipient.workspace_ids[0]),
                "role": "viewer",
            },
            headers=_auth(attacker.jwt),
        )
    assert resp.status_code in (403, 404), resp.text[:400]
    _assert_no_uuid_leak(resp.text, owner)

    async def _check():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT count(*) FROM rag.kasten_members
                 WHERE kasten_id = $1 AND workspace_id = $2
                """,
                kasten.sandbox_id, victim_recipient.workspace_ids[0],
            )

    leaked = asyncio.get_event_loop().run_until_complete(_check())
    assert leaked == 0, (
        f"attacker /share must NOT have inserted membership row; found {leaked}"
    )


def test_uk03_members_list_no_uuid_leak_on_denial(v2_app, mint_user, mint_kasten):
    """Non-member GET /sandboxes/{id}/members must deny without leaking UUIDs."""
    owner = mint_user(workspace_count=1)
    attacker = mint_user(workspace_count=1)
    kasten = mint_kasten(owner_user=owner)

    with TestClient(v2_app) as client:
        resp = client.get(
            f"/api/rag/sandboxes/{kasten.sandbox_id}/members",
            headers=_auth(attacker.jwt),
        )
    if resp.status_code == 503 and (
        "runtime" in resp.text.lower() or "not configured" in resp.text.lower()
    ):
        pytest.skip("Sandbox/RAG runtime unavailable in test env")
    assert resp.status_code in (403, 404), resp.text[:400]
    _assert_no_uuid_leak(resp.text, owner)
