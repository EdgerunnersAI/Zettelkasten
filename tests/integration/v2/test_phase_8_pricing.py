"""Phase 8 pricing-migration integration tests (live).

Covers billing.pricing_active_plan port (Task 1) + user_pricing repository
v2 routing (Task 2).
"""
from __future__ import annotations

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


async def test_pricing_active_plan_returns_subscribed_plan(mint_user, asyncpg_pool):
    """When the user has an active subscription, return its plan_id.

    Uses ``async def`` + ``await`` directly because ``asyncpg_pool`` is a
    ``pytest_asyncio.fixture`` (function-scoped, per-test event loop). The
    plan-doc ``asyncio.get_event_loop().run_until_complete(...)`` pattern
    fails here — that loop is the one already running this test, and asyncpg
    pools are bound to the loop they were created on.
    """
    user = mint_user(workspace_count=1)
    async with asyncpg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO billing.pricing_subscriptions
                (profile_id, plan_id, status, current_period_end)
            VALUES ($1, 'basic', 'active', now() + INTERVAL '1 month')
            """,
            user.profile_id,
        )

    client = get_v2_client()
    resp = client.schema("billing").rpc(
        "pricing_active_plan", {"p_profile_id": str(user.profile_id)}
    ).execute()
    assert resp.data == "basic", f"expected 'basic', got {resp.data!r}"
