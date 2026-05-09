"""Integration tests for content.upsert_canonical_zettel (Phase 1.C).

Covers the SECURITY DEFINER RPC shipped in `_v2/17_content_rpcs.sql`. Round-2
R2.6 mandates `ON CONFLICT DO UPDATE SET normalized_url = EXCLUDED.normalized_url`
(no-op self-assign) so the row is returned with xmax=0 for the inserter and
xmax!=0 for losers under concurrent contention. The race test (10 concurrent
calls -> exactly 1 was_new=True) is the spec-mandated correctness gate.

asyncpg is used directly because (a) the RPC takes bytea (content_hash) which
the supabase-py client can't bind cleanly, and (b) the race test requires
concurrent connections from the pool.

All tests marked @pytest.mark.live — they hit the live v2 Supabase project.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest


pytestmark = pytest.mark.live


_RPC_SQL = (
    "SELECT id, was_new FROM content.upsert_canonical_zettel"
    "($1::text, $2::bytea, $3::text, $4::text, $5::text, $6::date, $7::jsonb)"
)


def _fresh_url() -> str:
    return f"https://upsert-test-{uuid.uuid4().hex[:12]}.example.com/"


def _fresh_hash() -> bytes:
    # 32 bytes — same shape used elsewhere in the v2 test suite.
    return uuid.uuid4().bytes + uuid.uuid4().bytes


async def _delete_by_url(pool: asyncpg.Pool, url: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM content.canonical_zettels WHERE normalized_url = $1",
            url,
        )


async def _count_by_url(pool: asyncpg.Pool, url: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM content.canonical_zettels WHERE normalized_url = $1",
            url,
        )


async def _call_rpc(
    conn: asyncpg.Connection,
    *,
    url: str,
    content_hash: bytes,
    source_type: str = "web",
    title: str = "test-title",
    body_md: str = "body",
    publication_date: Any = None,
    source_metadata: str = "{}",
) -> asyncpg.Record:
    return await conn.fetchrow(
        _RPC_SQL,
        url, content_hash, source_type, title, body_md,
        publication_date, source_metadata,
    )


# ---------------------------------------------------------------------------
# 1. Single insert returns was_new=True; row visible in table.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_insert_returns_was_new_true(asyncpg_pool: asyncpg.Pool):
    url = _fresh_url()
    h = _fresh_hash()
    try:
        async with asyncpg_pool.acquire() as conn:
            row = await _call_rpc(conn, url=url, content_hash=h)
        assert row is not None
        assert row["was_new"] is True
        assert isinstance(row["id"], uuid.UUID)

        assert await _count_by_url(asyncpg_pool, url) == 1
    finally:
        await _delete_by_url(asyncpg_pool, url)


# ---------------------------------------------------------------------------
# 2. Second call with same (url, hash) returns was_new=False, same id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_call_same_pair_returns_was_new_false(asyncpg_pool: asyncpg.Pool):
    url = _fresh_url()
    h = _fresh_hash()
    try:
        async with asyncpg_pool.acquire() as conn:
            first = await _call_rpc(conn, url=url, content_hash=h)
            second = await _call_rpc(conn, url=url, content_hash=h)

        assert first["was_new"] is True
        assert second["was_new"] is False
        assert second["id"] == first["id"]

        # Still exactly one row.
        assert await _count_by_url(asyncpg_pool, url) == 1
    finally:
        await _delete_by_url(asyncpg_pool, url)


# ---------------------------------------------------------------------------
# 3. Same URL + DIFFERENT hash inserts a new row (UNIQUE is on the pair).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_url_different_hash_creates_new_row(asyncpg_pool: asyncpg.Pool):
    url = _fresh_url()
    h1 = _fresh_hash()
    h2 = _fresh_hash()
    assert h1 != h2
    try:
        async with asyncpg_pool.acquire() as conn:
            r1 = await _call_rpc(conn, url=url, content_hash=h1)
            r2 = await _call_rpc(conn, url=url, content_hash=h2)

        assert r1["was_new"] is True
        assert r2["was_new"] is True
        assert r2["id"] != r1["id"]

        assert await _count_by_url(asyncpg_pool, url) == 2
    finally:
        await _delete_by_url(asyncpg_pool, url)


# ---------------------------------------------------------------------------
# 4. Race test (Round-1 Amendment 2.0 mandate): 10 concurrent -> exactly 1 was_new=True.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_race_ten_concurrent_calls_exactly_one_was_new(asyncpg_pool: asyncpg.Pool):
    url = _fresh_url()
    h = _fresh_hash()

    async def _one() -> bool:
        async with asyncpg_pool.acquire() as conn:
            row = await _call_rpc(conn, url=url, content_hash=h)
            return row["was_new"]

    try:
        results = await asyncio.gather(*[_one() for _ in range(10)])
        true_count = sum(1 for r in results if r is True)
        false_count = sum(1 for r in results if r is False)
        assert true_count == 1, f"expected exactly 1 was_new=True, got {true_count}: {results}"
        assert false_count == 9, f"expected 9 was_new=False, got {false_count}: {results}"

        # Only one row should exist.
        assert await _count_by_url(asyncpg_pool, url) == 1
    finally:
        await _delete_by_url(asyncpg_pool, url)


# ---------------------------------------------------------------------------
# 5. NULL handling: p_publication_date NULL is fine (column is nullable).
#    p_source_metadata NULL violates NOT NULL on the column — the RPC should
#    raise; this test asserts the failure mode is a clear NOT-NULL violation
#    (rather than silently inserting an unexpected value).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_publication_date_ok(asyncpg_pool: asyncpg.Pool):
    url = _fresh_url()
    h = _fresh_hash()
    try:
        async with asyncpg_pool.acquire() as conn:
            row = await _call_rpc(
                conn, url=url, content_hash=h, publication_date=None,
            )
        assert row["was_new"] is True
    finally:
        await _delete_by_url(asyncpg_pool, url)


@pytest.mark.asyncio
async def test_null_source_metadata_raises_not_null(asyncpg_pool: asyncpg.Pool):
    url = _fresh_url()
    h = _fresh_hash()
    try:
        with pytest.raises(asyncpg.exceptions.NotNullViolationError):
            async with asyncpg_pool.acquire() as conn:
                # Pass NULL through the jsonb parameter — column has NOT NULL
                # DEFAULT '{}'::jsonb, but the RPC passes the parameter through
                # without coalescing, so NULL must trigger 23502.
                await conn.fetchrow(
                    _RPC_SQL,
                    url, h, "web", "t", "b", None, None,
                )
    finally:
        await _delete_by_url(asyncpg_pool, url)
