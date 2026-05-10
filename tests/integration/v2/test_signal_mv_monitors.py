"""Phase 8.5.B-3-monitors — live tests for signal MV cron + monitor views.

Verifies the operator-approved 3-hourly cadence and the data-driven monitor
surface used to determine optimal cron timing for the current user base.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


def test_cron_schedules_registered(asyncpg_pool):
    """Both refresh jobs exist with expected schedules."""
    import asyncio
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT jobname, schedule FROM cron.job WHERE jobname IN "
                "('refresh_kg_viz_weights', 'refresh_kasten_retrieval_signals') "
                "ORDER BY jobname"
            )
            return [(r["jobname"], r["schedule"]) for r in rows]
    schedules = asyncio.get_event_loop().run_until_complete(_check())
    assert ("refresh_kasten_retrieval_signals", "5 */3 * * *") in schedules, (
        f"retrieval signal cron should be 3-hourly post-37; got {schedules}"
    )
    assert ("refresh_kg_viz_weights", "15 3 * * *") in schedules, (
        f"viz cron should be daily 03:15 UTC; got {schedules}"
    )


def test_event_traffic_by_hour_view_queryable(asyncpg_pool):
    """View must exist + return rows whose schema matches expectations."""
    import asyncio
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='kg' AND table_name='event_traffic_by_hour' "
                "ORDER BY ordinal_position"
            )
            return [c["column_name"] for c in cols]
    cols = asyncio.get_event_loop().run_until_complete(_check())
    expected = {"hour_utc", "hour_ist", "event_count", "distinct_users",
                "distinct_workspaces", "last_7d", "last_30d"}
    assert expected.issubset(set(cols)), f"missing cols: {expected - set(cols)}"


def test_signal_mv_refresh_health_view(asyncpg_pool):
    """Health view returns FRESH for recently-refreshed MVs."""
    import asyncio
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            await conn.execute("SELECT kg.refresh_signal_mv('kg.kg_edge_viz_weights')")
            await conn.execute("SELECT kg.refresh_signal_mv('rag.kasten_retrieval_edge_signals')")
            rows = await conn.fetch(
                "SELECT mv_name, health_status, expected_cadence_seconds "
                "FROM kg.signal_mv_refresh_health ORDER BY mv_name"
            )
            return [(r["mv_name"], r["health_status"], r["expected_cadence_seconds"]) for r in rows]
    rows = asyncio.get_event_loop().run_until_complete(_check())
    assert len(rows) == 2
    for mv_name, status, cadence in rows:
        assert status == "FRESH", f"{mv_name}: expected FRESH, got {status}"
    cadences = {r[0]: r[2] for r in rows}
    assert cadences["kg.kg_edge_viz_weights"] == 86400, "viz cadence 24h"
    assert cadences["rag.kasten_retrieval_edge_signals"] == 10800, "retrieval cadence 3h"


def test_signal_mv_refresh_health_detects_stale(asyncpg_pool):
    """Backdate refresh log → health view returns STALE."""
    import asyncio
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            # Force the retrieval MV's logged refresh time to 5h ago (>3h cadence).
            await conn.execute(
                "UPDATE kg.mv_refresh_log SET refreshed_at = now() - INTERVAL '5 hours' "
                "WHERE mv_name = 'rag.kasten_retrieval_edge_signals'"
            )
            row = await conn.fetchrow(
                "SELECT health_status FROM kg.signal_mv_refresh_health "
                "WHERE mv_name = 'rag.kasten_retrieval_edge_signals'"
            )
            status = row["health_status"]
            # Restore so subsequent tests don't see STALE.
            await conn.execute("SELECT kg.refresh_signal_mv('rag.kasten_retrieval_edge_signals')")
            return status
    status = asyncio.get_event_loop().run_until_complete(_check())
    assert status == "STALE", f"expected STALE after 5h, got {status}"


def test_recommended_cron_low_traffic_hour_callable(asyncpg_pool):
    """Helper function returns rows or empty (INSUFFICIENT_DATA at low scale)."""
    import asyncio
    async def _check():
        async with asyncpg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT hour_utc, hour_ist, event_count, confidence "
                "FROM kg.recommended_cron_low_traffic_hour(1)"  # threshold=1 to surface anything
            )
            return rows
    rows = asyncio.get_event_loop().run_until_complete(_check())
    # At our scale we may have 0 events; just assert the function runs without error.
    assert rows is not None
