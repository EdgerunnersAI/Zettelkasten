"""Shared fixtures for v2 integration tests.

Tests in this directory MUST be marked @pytest.mark.live (they hit the live
Supabase project). The asyncpg_pool fixture connects to the direct port-5432
Postgres URL (NOT pgbouncer). The mint_user fixture creates fresh test users
and cleans them up at teardown.
"""
from __future__ import annotations

import uuid
import warnings
from typing import AsyncIterator
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from tests.v2.fixtures import MintedUser, mint_test_user_with_workspaces
from tests.v2.fixtures.users import delete_test_user
from website.core.supabase_v2.client import get_v2_database_url


@pytest_asyncio.fixture
async def asyncpg_pool() -> AsyncIterator[asyncpg.Pool]:
    """Direct port-5432 asyncpg pool for v2 integration tests.

    Function-scoped so the pool is bound to the same event loop as the test
    that uses it (pytest-asyncio runs each async test in its own loop in
    ``asyncio_mode = auto``; a session-scoped pool would raise
    ``got Future <…> attached to a different loop`` on the second test).
    Per-test pool create+close cost is ~50 ms — acceptable for the integration
    suite size.

    Refuses to start if the URL points at pgbouncer (port 6543) — see
    plan amendment about LISTEN port enforcement. Strict port parse (not a
    substring check) so credentials/host components containing "6543" cannot
    spoof the guard.
    """
    url = get_v2_database_url(listen=False)
    parsed = urlsplit(url)
    if parsed.port == 6543:
        raise ValueError(
            "asyncpg_pool requires direct port 5432 (not pgbouncer 6543)."
        )
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def created_auth_user_ids() -> list[uuid.UUID]:
    """Per-test list of ``auth.users.id`` UUIDs the test minted; cleaned up at teardown.

    Cleanup is best-effort (does not abort on the first failure) but every
    failure is collected and surfaced via ``warnings.warn`` so a pool exhaustion
    or auth outage cannot silently leak users without test output.
    """
    created: list[uuid.UUID] = []
    yield created
    errors: list[tuple[uuid.UUID, BaseException]] = []
    for auth_user_id in created:
        try:
            delete_test_user(auth_user_id)
        except Exception as exc:  # noqa: BLE001 — collect and report at end
            errors.append((auth_user_id, exc))
    if errors:
        msg = "; ".join(f"{aid}: {exc!r}" for aid, exc in errors)
        warnings.warn(
            f"Test-user cleanup failed for {len(errors)} user(s): {msg}",
            stacklevel=1,
        )


@pytest.fixture
def mint_user(created_auth_user_ids: list[uuid.UUID]):
    """Factory: returns a callable that mints a user AND records it for cleanup.

    Usage:
        def test_x(mint_user):
            user = mint_user(workspace_count=2)
            # user.auth_user_id, user.profile_id, user.workspace_ids, user.jwt
    """
    def _mint(*, workspace_count: int = 1) -> MintedUser:
        user = mint_test_user_with_workspaces(workspace_count=workspace_count)
        # Record auth_user_id (NOT profile_id) — delete_test_user requires the
        # auth.users.id, and the FK invariant making them equal today is not
        # something cleanup should depend on.
        created_auth_user_ids.append(user.auth_user_id)
        return user
    return _mint
