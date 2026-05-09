"""Shared fixtures for v2 integration tests.

Tests in this directory MUST be marked @pytest.mark.live (they hit the live
Supabase project). The asyncpg_pool fixture connects to the direct port-5432
Postgres URL (NOT pgbouncer). The mint_user fixture creates fresh test users
and cleans them up at teardown.
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from tests.v2.fixtures import mint_test_user_with_workspaces
from tests.v2.fixtures.users import delete_test_user
from website.core.supabase_v2.client import get_v2_database_url


@pytest_asyncio.fixture(scope="session")
async def asyncpg_pool() -> AsyncIterator[asyncpg.Pool]:
    """Direct port-5432 asyncpg pool for v2 integration tests.

    Refuses to start if the URL points at pgbouncer (port 6543) — see
    plan amendment about LISTEN port enforcement.
    """
    url = get_v2_database_url(listen=False)
    if ":6543" in url:
        raise ValueError(
            "asyncpg_pool requires direct port 5432 (not pgbouncer 6543)."
        )
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def created_test_users() -> list[uuid.UUID]:
    """Per-test list of auth user UUIDs the test minted; cleaned up at teardown."""
    created: list[uuid.UUID] = []
    yield created
    for auth_user_id in created:
        try:
            delete_test_user(auth_user_id)
        except Exception:
            import traceback
            traceback.print_exc()


@pytest.fixture
def mint_user(created_test_users: list[uuid.UUID]):
    """Factory: returns a callable that mints a user AND records it for cleanup.

    Usage:
        def test_x(mint_user):
            profile_id, workspace_ids, jwt = mint_user(workspace_count=2)
    """
    def _mint(*, workspace_count: int = 1):
        profile_id, workspace_ids, jwt = mint_test_user_with_workspaces(
            workspace_count=workspace_count
        )
        created_test_users.append(profile_id)
        return profile_id, workspace_ids, jwt
    return _mint
