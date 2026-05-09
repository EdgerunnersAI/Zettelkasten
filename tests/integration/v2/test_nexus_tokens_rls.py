"""Integration tests for pipelines.nexus_provider_tokens RLS (Phase 1.B).

Covers the SELECT/INSERT/UPDATE/DELETE policies and the service-role bypass
defined in `_v2/16_nexus_tokens.sql`. The user-scoped tests use the supabase-py
user client so JWT app_metadata.workspace_ids drive `core.jwt_workspace_ids()`;
the service-role / setup paths use the asyncpg pool (direct port 5432) which
runs as the postgres superuser and bypasses RLS.

All tests marked @pytest.mark.live — they hit the live v2 Supabase project.
"""
from __future__ import annotations

import time
import uuid

import asyncpg
import pytest
from postgrest.exceptions import APIError

from website.core.supabase_v2.client import get_v2_user_client


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _service_insert_token(
    pool: asyncpg.Pool,
    *,
    profile_id: uuid.UUID,
    workspace_id: uuid.UUID,
    provider: str,
    encrypted_token: bytes = b"\x01\x02\x03",
    refresh_token: bytes | None = None,
    expires_at=None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pipelines.nexus_provider_tokens
                (profile_id, workspace_id, provider, encrypted_token,
                 refresh_token, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            profile_id, workspace_id, provider,
            encrypted_token, refresh_token, expires_at,
        )


async def _count_tokens(
    pool: asyncpg.Pool, *, profile_id: uuid.UUID, provider: str | None = None
) -> int:
    async with pool.acquire() as conn:
        if provider is None:
            row = await conn.fetchrow(
                "SELECT count(*)::int AS n FROM pipelines.nexus_provider_tokens WHERE profile_id = $1",
                profile_id,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT count(*)::int AS n FROM pipelines.nexus_provider_tokens
                 WHERE profile_id = $1 AND provider = $2
                """,
                profile_id, provider,
            )
        return row["n"]


def _is_rls_denial(exc: BaseException) -> bool:
    msg = str(exc).lower()
    code = getattr(exc, "code", None)
    return (
        "row-level security" in msg
        or "violates row-level security" in msg
        or "42501" in msg
        or code == "42501"
        or code == "PGRST301"
    )


# ---------------------------------------------------------------------------
# 1. Member sees own row (SELECT policy + service-role insert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_sees_own_token_row(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    await _service_insert_token(
        asyncpg_pool,
        profile_id=user.profile_id,
        workspace_id=ws_id,
        provider="github",
    )

    client = get_v2_user_client(user.jwt)
    resp = (
        client.schema("pipelines")
        .table("nexus_provider_tokens")
        .select("profile_id, workspace_id, provider")
        .eq("provider", "github")
        .execute()
    )
    rows = resp.data or []
    assert len(rows) == 1, f"expected 1 visible row for owner, got {rows!r}"
    assert uuid.UUID(rows[0]["profile_id"]) == user.profile_id
    assert uuid.UUID(rows[0]["workspace_id"]) == ws_id
    assert rows[0]["provider"] == "github"


# ---------------------------------------------------------------------------
# 2. Non-member denied (RLS SELECT predicate filters foreign workspace rows)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_member_cannot_see_token(mint_user, asyncpg_pool):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    ws_id = owner.workspace_ids[0]
    assert ws_id not in intruder.workspace_ids

    await _service_insert_token(
        asyncpg_pool,
        profile_id=owner.profile_id,
        workspace_id=ws_id,
        provider="github",
    )

    intruder_client = get_v2_user_client(intruder.jwt)
    resp = (
        intruder_client.schema("pipelines")
        .table("nexus_provider_tokens")
        .select("profile_id, workspace_id, provider")
        .eq("workspace_id", str(ws_id))
        .execute()
    )
    # RLS filters silently — intruder sees an empty result, not an error.
    assert (resp.data or []) == [], f"expected no rows for intruder, got {resp.data!r}"


# ---------------------------------------------------------------------------
# 3. Member can INSERT with own workspace_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_can_insert_own_workspace(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    client = get_v2_user_client(user.jwt)
    resp = (
        client.schema("pipelines")
        .table("nexus_provider_tokens")
        .insert(
            {
                "profile_id": str(user.profile_id),
                "workspace_id": str(ws_id),
                "provider": "github",
                # bytea via PostgREST: hex-prefixed string is rejected by
                # supabase-py's JSON encoder; pass as a plain string and let
                # PostgreSQL implicit-cast it. Use a known ASCII payload so the
                # later asyncpg readback is straightforward.
                "encrypted_token": "\\x010203",
            }
        )
        .execute()
    )
    rows = resp.data or []
    assert len(rows) == 1, f"expected insert to return 1 row, got {rows!r}"

    assert await _count_tokens(asyncpg_pool, profile_id=user.profile_id, provider="github") == 1


# ---------------------------------------------------------------------------
# 4. Insert with foreign workspace_id is rejected by WITH CHECK
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_cannot_insert_foreign_workspace(mint_user):
    owner = mint_user(workspace_count=1)
    intruder = mint_user(workspace_count=1)
    foreign_ws = owner.workspace_ids[0]
    assert foreign_ws not in intruder.workspace_ids

    intruder_client = get_v2_user_client(intruder.jwt)
    with pytest.raises((APIError, Exception)) as exc_info:
        intruder_client.schema("pipelines").table("nexus_provider_tokens").insert(
            {
                "profile_id": str(intruder.profile_id),
                "workspace_id": str(foreign_ws),
                "provider": "github",
                "encrypted_token": "\\x010203",
            }
        ).execute()
    assert _is_rls_denial(exc_info.value), f"expected RLS denial, got {exc_info.value!r}"


# ---------------------------------------------------------------------------
# 5. Service-role bypass: SELECT/INSERT/UPDATE/DELETE regardless of workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_role_bypass_full_crud(mint_user, asyncpg_pool):
    """Service-role asyncpg connection is the postgres superuser — RLS is
    bypassed automatically for any role with BYPASSRLS, and the
    nexus_tokens_service_all policy is the dedicated escape hatch for the
    PostgREST service-role JWT path. We exercise the asyncpg path here
    (which is what server-side code uses) for SELECT/INSERT/UPDATE/DELETE.
    """
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    # INSERT
    await _service_insert_token(
        asyncpg_pool,
        profile_id=user.profile_id,
        workspace_id=ws_id,
        provider="reddit",
        encrypted_token=b"\xde\xad\xbe\xef",
    )

    # SELECT
    async with asyncpg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT encrypted_token FROM pipelines.nexus_provider_tokens
             WHERE profile_id = $1 AND provider = $2
            """,
            user.profile_id, "reddit",
        )
        assert row is not None
        assert bytes(row["encrypted_token"]) == b"\xde\xad\xbe\xef"

        # UPDATE
        await conn.execute(
            """
            UPDATE pipelines.nexus_provider_tokens
               SET encrypted_token = $3
             WHERE profile_id = $1 AND provider = $2
            """,
            user.profile_id, "reddit", b"\xca\xfe",
        )
        row2 = await conn.fetchrow(
            """
            SELECT encrypted_token FROM pipelines.nexus_provider_tokens
             WHERE profile_id = $1 AND provider = $2
            """,
            user.profile_id, "reddit",
        )
        assert bytes(row2["encrypted_token"]) == b"\xca\xfe"

        # DELETE
        await conn.execute(
            """
            DELETE FROM pipelines.nexus_provider_tokens
             WHERE profile_id = $1 AND provider = $2
            """,
            user.profile_id, "reddit",
        )

    assert await _count_tokens(asyncpg_pool, profile_id=user.profile_id, provider="reddit") == 0


# ---------------------------------------------------------------------------
# 6. CASCADE: deleting the auth user cascades through profiles -> tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_delete_via_profile(mint_user, asyncpg_pool, created_auth_user_ids):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    await _service_insert_token(
        asyncpg_pool,
        profile_id=user.profile_id,
        workspace_id=ws_id,
        provider="github",
    )
    assert await _count_tokens(asyncpg_pool, profile_id=user.profile_id) == 1

    # Delete the profile directly — auth.users -> profiles -> tokens cascade.
    # We delete the profile row (not the auth user) so the conftest cleanup
    # still finds and removes the auth user; otherwise the cleanup would race
    # with this assertion. profile -> token CASCADE is what we're testing.
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM core.profiles WHERE id = $1", user.profile_id
        )

    assert await _count_tokens(asyncpg_pool, profile_id=user.profile_id) == 0


# ---------------------------------------------------------------------------
# 7. updated_at trigger bumps timestamp on UPDATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updated_at_trigger_bumps_timestamp(mint_user, asyncpg_pool):
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]
    await _service_insert_token(
        asyncpg_pool,
        profile_id=user.profile_id,
        workspace_id=ws_id,
        provider="github",
    )
    async with asyncpg_pool.acquire() as conn:
        before = await conn.fetchval(
            """
            SELECT updated_at FROM pipelines.nexus_provider_tokens
             WHERE profile_id = $1 AND provider = $2
            """,
            user.profile_id, "github",
        )
    # Sleep 50ms so the trigger-driven now() is strictly greater than `before`
    # even on fast/coarse-clock platforms.
    time.sleep(0.05)
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pipelines.nexus_provider_tokens
               SET encrypted_token = $3
             WHERE profile_id = $1 AND provider = $2
            """,
            user.profile_id, "github", b"\x99",
        )
        after = await conn.fetchval(
            """
            SELECT updated_at FROM pipelines.nexus_provider_tokens
             WHERE profile_id = $1 AND provider = $2
            """,
            user.profile_id, "github",
        )
    assert after > before, f"updated_at not bumped: before={before!r} after={after!r}"
