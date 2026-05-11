"""UP-16: creating a user must NOT pre-populate entitlement/usage tables.

Pricing-authority rule (CLAUDE.md): NEVER seed entitlements directly.
Every new user starts at zero rows in every ``billing.pricing_*`` usage
table; subscriptions/consumption land only via the real subscribe path or
real metered actions. If a future migration or trigger auto-seeds a row,
this test fails and surfaces the violation to the operator.

The tables we sweep (per ``supabase/website/_v2/06_billing_schema.sql``):
- ``billing.pricing_entitlement_consumption`` (profile_id-scoped usage)
- ``billing.pricing_subscriptions`` (profile_id-scoped subscription state)
- ``billing.pricing_balances`` (profile_id-scoped pack credits)

We deliberately do NOT sweep ``billing.pricing_plan_entitlements`` because
that table is the (immutable) plan-tier definition catalog — it MUST contain
rows for {free, basic, max}; those rows are global plan rows, not per-user
state. The no-seed invariant only applies to per-profile state.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


# Tables that MUST have zero rows for a fresh user. (table, profile_id_column).
_PER_PROFILE_TABLES = [
    ("billing.pricing_entitlement_consumption", "profile_id"),
    ("billing.pricing_subscriptions", "profile_id"),
    ("billing.pricing_balances", "profile_id"),
    ("billing.pricing_billing_profiles", "profile_id"),
    ("billing.pricing_orders", "profile_id"),
    ("billing.pricing_refunds", "profile_id"),
    ("billing.pricing_disputes", "profile_id"),
]


# Tests use ``async def`` so the asyncpg_pool (function-scoped, bound to the
# pytest-asyncio loop) can be awaited directly. asyncio.run() would create a
# new loop and trip "Future attached to a different loop".
async def test_fresh_user_has_no_pricing_rows(mint_user, asyncpg_pool):
    """A freshly minted user has zero rows in every per-profile pricing table.

    Mints a user, then runs ``SELECT COUNT(*)`` against each per-profile
    pricing table. Any non-zero row count fails the test with the offending
    table name + row count, so the operator can pin the seeding source.
    """
    user = mint_user()

    counts: dict[str, int] = {}
    async with asyncpg_pool.acquire() as conn:
        for table, pid_col in _PER_PROFILE_TABLES:
            # f-string for table+column identifiers is safe here: both come
            # from the in-test allowlist above, not user input.
            row = await conn.fetchval(
                f"SELECT COUNT(*) FROM {table} WHERE {pid_col} = $1",
                user.profile_id,
            )
            counts[table] = int(row or 0)

    seeded = {t: n for t, n in counts.items() if n != 0}
    assert not seeded, (
        f"Fresh user {user.profile_id} has pre-seeded rows in: {seeded}. "
        "Pricing-authority rule violation — STOP and surface to operator."
    )


async def test_fresh_user_has_no_active_subscription(mint_user, asyncpg_pool):
    """Belt-and-braces: no row in pricing_subscriptions of any status.

    Catches the case where an auto-subscribe trigger inserts a row at a
    weaker status the broad COUNT(*) might miss if filtered.
    """
    user = mint_user()

    async with asyncpg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, plan_id FROM billing.pricing_subscriptions "
            "WHERE profile_id = $1",
            user.profile_id,
        )

    assert rows == [], (
        f"Fresh user has subscription row(s): {[dict(r) for r in rows]}. "
        "Auto-subscribe is explicitly forbidden by pricing-authority rule."
    )
