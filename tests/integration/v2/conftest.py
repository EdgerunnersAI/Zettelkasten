"""Shared fixtures for v2 integration tests.

Tests in this directory MUST be marked @pytest.mark.live (they hit the live
Supabase project). The asyncpg_pool fixture connects to the direct port-5432
Postgres URL (NOT pgbouncer). The mint_user fixture creates fresh test users
and cleans them up at teardown.
"""
from __future__ import annotations

import os
import re
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

# Phase 7.3b: end-of-session backstop. Per-test fixtures clean up via
# created_auth_user_ids, but a hard crash (KeyboardInterrupt, segfault, OOM
# on the worker) skips teardown and leaks e2e users. The mint pattern is
# e2e-{uuid.uuid4().hex[:8]}@test.com; allow 6-12 hex chars for forward-
# compat if the prefix length ever changes.
_E2E_EMAIL_PATTERN = re.compile(r"^e2e-[0-9a-f]{6,12}@test\.com$")


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

    Phase 8.5.R2 amendment (T1): we run ``account_purge.purge_user_dependencies``
    BEFORE ``auth.admin.delete_user`` to pre-clean FK-bound rows the GoTrue
    admin API doesn't cascade through (rag.retrieval_feedback_events,
    billing.pricing_subscriptions, rag.kasten_members). Without this, content-
    seeding tests left ``AuthApiError('Database error deleting user')``
    warnings that the session-finish backstop swept up — the per-test purge
    eliminates the noise so real test failures aren't drowned in teardown
    noise. Canonical pattern per Supabase Discussion #28776 + storage#65:
    auth.admin.delete_user has no cascade flag in 2024-2026.
    """
    created: list[uuid.UUID] = []
    yield created
    errors: list[tuple[uuid.UUID, BaseException]] = []
    # Lazy import — account_purge pulls supabase client init; only needed at
    # teardown so we defer the cost until the fixture is actually used.
    from website.core.account_purge import purge_user_dependencies
    for auth_user_id in created:
        try:
            # Pre-purge FK-bound rows the admin API doesn't cascade through.
            # profile_id == auth_user_id today (handle_new_auth_user trigger
            # invariant); if the invariant ever breaks, the purge becomes a
            # no-op and admin.delete_user falls back to its prior behaviour —
            # the session-finish backstop sweep then handles the residue.
            try:
                purge_user_dependencies(auth_user_id)
            except Exception as purge_exc:  # noqa: BLE001 — best-effort
                warnings.warn(
                    f"purge_user_dependencies({auth_user_id}) failed: {purge_exc!r}",
                    stacklevel=1,
                )
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


def pytest_sessionfinish(session, exitstatus):
    """Sweep any leftover ``e2e-*@test.com`` users from this session.

    Backstop for per-test cleanup (which already runs via
    ``created_auth_user_ids``). When a test crashes hard or the runner is
    killed, teardown is skipped and fixture users leak — this hook catches
    them at session boundary. Set ``SKIP_TEST_FIXTURE_SWEEP=1`` to opt out
    (e.g., when running across two parallel sessions sharing a project).

    Best-effort only: errors here must NOT fail the session.
    """
    if os.environ.get("SKIP_TEST_FIXTURE_SWEEP"):
        return

    try:
        from website.core.supabase_v2.client import get_v2_client
    except Exception as exc:  # noqa: BLE001 — config missing → silently skip
        print(
            f"\n[pytest_sessionfinish] skip sweep "
            f"({type(exc).__name__}: {exc})"
        )
        return

    try:
        client = get_v2_client()
        leftover = []
        page = 1
        while True:
            resp = client.auth.admin.list_users(page=page, per_page=200)
            users = resp if isinstance(resp, list) else getattr(resp, "users", [])
            if not users:
                break
            for u in users:
                email = getattr(u, "email", None) or ""
                if email and _E2E_EMAIL_PATTERN.match(email):
                    leftover.append(u)
            if len(users) < 200:
                break
            page += 1
            if page > 50:  # hard safety cap (10k users)
                break

        for u in leftover:
            try:
                client.auth.admin.delete_user(u.id)
            except Exception:  # noqa: BLE001 — best-effort
                pass

        if leftover:
            print(
                f"\n[pytest_sessionfinish] swept {len(leftover)} leftover "
                f"test-fixture user(s)"
            )
    except Exception as exc:  # noqa: BLE001 — never fail the session
        print(
            f"\n[pytest_sessionfinish] cleanup error: "
            f"{type(exc).__name__}: {exc}"
        )
