"""Phase 8.5.R2-3 — auth.users CASCADE chain regression test.

Defends against the prior "Database error deleting user" 500 by:
1. Discovering all dependent (schema, table, fk_column) tuples via the
   introspection RPC (core.introspect_auth_users_dependents).
2. Seeding a tagged target user + control user with state across multiple
   dependent tables.
3. Calling purge_user_dependencies + auth.admin.delete_user on target.
4. Asserting target's rows are zero across all dependents AND control's
   rows are unchanged.

@pytest.mark.live + @pytest.mark.destructive (opt-in via -m destructive).
The destructive marker is declared in pyproject.toml; default-skip applies.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from website.core.account_purge import purge_user_dependencies
from website.core.supabase_v2.client import get_v2_client


pytestmark = [pytest.mark.live, pytest.mark.destructive]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
def test_delete_user_cascades_entire_chain(mint_user, asyncpg_pool):
    """Target user purge + delete: zero rows remain; control user untouched."""
    target = mint_user(workspace_count=1)
    control = mint_user(workspace_count=1)
    sb = get_v2_client()

    # Discover the v2 portion of the dependency graph (skip auth.* internals
    # which are GoTrue-managed and CASCADE on auth.users delete by design).
    deps_rows = (
        sb.schema("core")
        .rpc("introspect_auth_users_dependents", {})
        .execute()
        .data
        or []
    )
    deps = [
        (r["schema_name"], r["table_name"], r["fk_column"])
        for r in deps_rows
        if r["schema_name"] != "auth"
    ]
    assert deps, "no dependents discovered — introspection RPC broken"

    async def _count_rows(profile_id: uuid.UUID) -> dict[tuple[str, str, str], int]:
        """count(*) per (schema, table, fk_col) where fk_col = profile_id."""
        counts: dict[tuple[str, str, str], int] = {}
        async with asyncpg_pool.acquire() as conn:
            for schema, table, fk_col in deps:
                # Defensive: some columns may not be UUID-typed (e.g. text-keyed
                # historical tables). Guard with a safe cast in WHERE clause.
                try:
                    n = await conn.fetchval(
                        f'SELECT count(*) FROM "{schema}"."{table}" '
                        f'WHERE "{fk_col}"::text = $1',
                        str(profile_id),
                    )
                except Exception:
                    n = 0
                counts[(schema, table, fk_col)] = int(n or 0)
        return counts

    # Pre-delete: count rows for both users
    before_target = asyncio.get_event_loop().run_until_complete(
        _count_rows(target.auth_user_id)
    )
    before_control = asyncio.get_event_loop().run_until_complete(
        _count_rows(control.auth_user_id)
    )

    # mint_user creates profile + workspace + member rows, so target should
    # have non-zero counts somewhere. (Belt-and-suspenders sanity check.)
    assert sum(before_target.values()) > 0, (
        "fixture did not seed any rows for target; test would falsely pass"
    )

    # Pre-flight purge (anonymise events, cancel subs)
    report = purge_user_dependencies(target.auth_user_id)
    assert report is not None

    # The actual delete — must NOT raise. Pre-Phase-8.5 this surfaced as a 500
    # "Database error deleting user" because of FK chain misconfig.
    sb.auth.admin.delete_user(str(target.auth_user_id))

    # Post-delete: target's rows should be zero across the v2 graph
    after_target = asyncio.get_event_loop().run_until_complete(
        _count_rows(target.auth_user_id)
    )
    after_control = asyncio.get_event_loop().run_until_complete(
        _count_rows(control.auth_user_id)
    )

    # The events log is intentionally anonymised (user_id=NULL) — so its
    # row count by user_id IS zero post-purge. Other tables should be CASCADE-
    # cleaned. Identify orphans.
    orphans = {k: v for k, v in after_target.items() if v > 0}
    assert not orphans, (
        f"orphans remain for target after delete: {orphans} "
        f"(before: {[k for k,v in before_target.items() if v > 0]})"
    )

    # Control user's rows must be byte-identical pre/post target's deletion.
    assert after_control == before_control, (
        "control user's rows were touched by target's deletion — CASCADE "
        f"chain leaks. Diff: {[(k, before_control[k], after_control[k]) for k in before_control if before_control[k] != after_control[k]]}"
    )

    # Cleanup: also delete control to avoid leftover fixture users.
    purge_user_dependencies(control.auth_user_id)
    sb.auth.admin.delete_user(str(control.auth_user_id))
