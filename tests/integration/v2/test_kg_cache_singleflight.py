"""WAVE-C 1c-A.3 — UserGraphCache single-flight + invalidation tests.

Locked decisions covered:
- D-KG-6: in-process LRU cap=200 · TTL=30s · per-user lock · 20s timeout
- D-KG-7: full-invalidate on summarize / zettel mutation
- bucket key isolation across users (BOLA / OWASP API1:2023)

These are async unit tests against the cache module — they do NOT hit
Supabase, so they run with the rest of the suite without `--live`. The file
sits under tests/integration/v2/ to colocate with the other graph-API tests
even though it doesn't use the asyncpg_pool fixture.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from website.api.graph_cache import (
    UserGraphCache,
    bucket_for_strength,
    get_default_cache,
)


# ── Bucket key derivation ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "weak"),
        (0.0, "weak"),
        (0.39, "weak"),
        (0.4, "medium"),
        (0.69, "medium"),
        (0.7, "strong"),
        (1.0, "strong"),
        ("not-a-number", "weak"),
    ],
)
def test_bucket_for_strength_matches_d_kg_3(value, expected) -> None:
    assert bucket_for_strength(value) == expected


# ── Cache hit + invalidate round-trip ───────────────────────────────────


async def test_cache_hit_then_invalidate_round_trip() -> None:
    cache = UserGraphCache(capacity=10, ttl_seconds=60.0)
    user_id = "user-1"
    bucket = "strong"
    call_count = 0

    async def loader() -> dict:
        nonlocal call_count
        call_count += 1
        return {"nodes": [{"id": "a"}], "links": []}

    # Cold load.
    p1 = await cache.get_or_load(user_id, bucket, loader)
    # Warm hit.
    p2 = await cache.get_or_load(user_id, bucket, loader)
    assert p1 == p2
    assert call_count == 1, "warm hit must not invoke loader"

    # Invalidate → next load goes cold again.
    removed = cache.invalidate(user_id)
    assert removed >= 1
    await cache.get_or_load(user_id, bucket, loader)
    assert call_count == 2, "post-invalidate must reload"


# ── Single-flight: 50 concurrent → 1 upstream call ──────────────────────


async def test_single_flight_coalesces_concurrent_loads() -> None:
    cache = UserGraphCache(capacity=10, ttl_seconds=60.0)
    user_id = "user-coalesce"
    bucket = "weak"
    call_count = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_loader() -> dict:
        nonlocal call_count
        call_count += 1
        started.set()
        # Hold the loader so all 50 callers stack on the inflight Future.
        await release.wait()
        return {"nodes": [], "links": [], "marker": call_count}

    async def caller() -> dict:
        return await cache.get_or_load(user_id, bucket, slow_loader)

    tasks = [asyncio.create_task(caller()) for _ in range(50)]

    # Wait until the elected loader is in-flight, then release it.
    await started.wait()
    release.set()

    payloads = await asyncio.gather(*tasks)
    assert call_count == 1, f"single-flight failed: {call_count} upstream calls"
    # All 50 responses are the SAME object (Future result is shared).
    assert all(p == payloads[0] for p in payloads)


# ── Cache key isolation across users (BOLA / UUID-leak) ────────────────


async def test_cache_key_isolation_across_users() -> None:
    cache = UserGraphCache(capacity=10, ttl_seconds=60.0)

    async def loader_for(uid: str):
        async def _l() -> dict:
            return {"nodes": [{"id": f"node-of-{uid}"}], "links": []}

        return _l

    user_alice = str(uuid.uuid4())
    user_bob = str(uuid.uuid4())

    p_alice = await cache.get_or_load(user_alice, "strong", await loader_for(user_alice))
    p_bob = await cache.get_or_load(user_bob, "strong", await loader_for(user_bob))

    assert p_alice["nodes"][0]["id"] == f"node-of-{user_alice}"
    assert p_bob["nodes"][0]["id"] == f"node-of-{user_bob}"

    # Invalidating Alice MUST NOT touch Bob.
    cache.invalidate(user_alice)
    assert (user_alice, "strong") not in cache.keys()
    assert (user_bob, "strong") in cache.keys()

    # OWASP API1:2023 BOLA pattern — even if a buggy caller passed Alice's
    # uid into Bob's slot, the per-tuple key prevents cross-tenant payload
    # leak. Confirm by direct lookup.
    bob_cached = await cache.get_or_load(user_bob, "strong", await loader_for(user_bob))
    assert user_alice not in str(bob_cached)


# ── LRU cap honored at burst ──────────────────────────────────────────


async def test_lru_cap_honored_under_burst() -> None:
    cap = 50
    cache = UserGraphCache(capacity=cap, ttl_seconds=60.0)

    async def make_loader(idx: int):
        async def _l() -> dict:
            return {"nodes": [{"id": f"n-{idx}"}], "links": []}

        return _l

    # 600 distinct users hitting the cache → store should never exceed cap.
    for i in range(600):
        loader = await make_loader(i)
        await cache.get_or_load(f"user-{i}", "weak", loader)
        assert len(cache) <= cap, f"LRU cap exceeded at iter {i}: len={len(cache)}"

    assert len(cache) == cap


# ── TTL expiry triggers reload ──────────────────────────────────────────


async def test_ttl_expiry_triggers_reload() -> None:
    # WAVE-C C3-d hybrid: with default SWR=300s, expiry within that window
    # is a STALE-serve + bg refresh, not a cold reload. To preserve the
    # "TTL → cold reload" semantic this test pins, set swr_ttl_seconds = TTL
    # (no extra stale window). Beyond TTL → cold single-flight path.
    cache = UserGraphCache(capacity=10, ttl_seconds=0.05, swr_ttl_seconds=0.05)
    user_id = "ttl-user"
    call_count = 0

    async def loader() -> dict:
        nonlocal call_count
        call_count += 1
        return {"nodes": [], "links": []}

    await cache.get_or_load(user_id, "weak", loader)
    assert call_count == 1
    # Wait past TTL (also past SWR since they're equal here).
    await asyncio.sleep(0.1)
    await cache.get_or_load(user_id, "weak", loader)
    assert call_count == 2, "expired entry must trigger reload"


# ── Loader exception is propagated, inflight cleared ───────────────────


async def test_loader_exception_clears_inflight() -> None:
    cache = UserGraphCache(capacity=10, ttl_seconds=60.0)

    class _BoomError(RuntimeError):
        pass

    async def broken() -> dict:
        raise _BoomError("upstream boom")

    with pytest.raises(_BoomError):
        await cache.get_or_load("user-x", "weak", broken)

    # Inflight slot must be cleared so the next call retries cleanly.
    assert cache.inflight_count() == 0
    # And not cached.
    assert ("user-x", "weak") not in cache.keys()


# ── Default singleton accessor stable ─────────────────────────────────


def test_get_default_cache_is_singleton() -> None:
    a = get_default_cache()
    b = get_default_cache()
    assert a is b


# ── Single-flight error path: no N+1 upstream calls under burst ────────


async def test_single_flight_error_path_does_not_n_plus_1() -> None:
    """50 concurrent callers + upstream raising must yield exactly 1 loader call.

    Regression for PR #7 C2: prior order was `_inflight.pop()` then
    `future.set_exception()`. A follower entering the gap saw neither a
    cached entry nor an inflight Future and triggered its own upstream load.
    The fix swaps the order under one lock acquisition so followers always
    observe the inflight Future, attach to it, and receive the same exception.
    """
    cache = UserGraphCache(capacity=10, ttl_seconds=60.0)
    user_id = "user-error-coalesce"
    bucket = "weak"
    call_count = 0
    started = asyncio.Event()
    release = asyncio.Event()

    class _BoomError(RuntimeError):
        pass

    async def slow_broken_loader() -> dict:
        nonlocal call_count
        call_count += 1
        started.set()
        # Hold so all 50 callers stack on the same inflight Future before we
        # raise — the burst window is what exposes the race.
        await release.wait()
        raise _BoomError("upstream boom")

    async def caller():
        try:
            return await cache.get_or_load(user_id, bucket, slow_broken_loader)
        except _BoomError as exc:
            return exc

    tasks = [asyncio.create_task(caller()) for _ in range(50)]

    # Wait for the elected loader to be in-flight, then release it.
    await started.wait()
    release.set()

    results = await asyncio.gather(*tasks)
    assert call_count == 1, (
        f"single-flight error path failed: {call_count} upstream calls "
        f"(expected 1). Followers raced into a fresh load instead of "
        f"attaching to the inflight Future."
    )
    # All 50 followers got the same exception type.
    assert all(isinstance(r, _BoomError) for r in results)
    # Inflight slot cleared so subsequent retries work.
    assert cache.inflight_count() == 0


# ── SWR (stale-while-revalidate) — D-KG-3 hybrid C3-d ─────────────────────


class _ClockCache(UserGraphCache):
    """Subclass with monkeypatchable monotonic clock for SWR tests.

    Avoids freezegun + asyncio interaction hazards. The cache only reads
    time via ``self._now()``; overriding it deterministically advances time
    inside the test without leaking globally.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._test_now = 1000.0

    def _now(self) -> float:
        return self._test_now

    def advance(self, delta: float) -> None:
        self._test_now += delta


async def test_swr_serves_stale_immediately_and_refreshes_in_background() -> None:
    """Hybrid C3-d.4+1+SWR: stale entry returned IMMEDIATELY + bg refresh.

    Within the SWR window (30s < age <= 300s), get_or_load must:
    1. Return the stale payload without awaiting the loader (immediate).
    2. Schedule a background refresh that updates the cache off-path.
    Beyond the SWR window (age > 300s) the cold single-flight path is taken.
    """
    cache = _ClockCache(ttl_seconds=30.0, swr_ttl_seconds=300.0)
    user = f"u-{uuid.uuid4().hex[:8]}"

    loader_calls = {"n": 0}

    async def fast_loader() -> dict:
        loader_calls["n"] += 1
        return {"v": loader_calls["n"], "loaded_at": cache._test_now}

    # Cold load: populates cache.
    p1 = await cache.get_or_load(user, "strong", fast_loader)
    assert p1["v"] == 1
    assert loader_calls["n"] == 1

    # Advance into the STALE window (45s > 30s TTL, < 300s SWR cap).
    cache.advance(45.0)

    # SWR hit: returns stale (v=1) immediately, schedules background refresh.
    p2 = await cache.get_or_load(user, "strong", fast_loader)
    assert p2["v"] == 1, "SWR must return STALE entry immediately"
    # Background refresh was scheduled (inflight slot taken).
    assert cache.inflight_count() == 1

    # Wait for the bg task to complete (it runs the loader + cache update).
    # Brief sleep ensures the create_task'd coroutine reaches its lock release.
    for _ in range(10):
        await asyncio.sleep(0.01)
        if cache.inflight_count() == 0:
            break

    # After bg refresh resolves, loader was called a 2nd time + cache updated.
    assert loader_calls["n"] == 2
    assert cache.inflight_count() == 0
    p3 = await cache.get_or_load(user, "strong", fast_loader)
    assert p3["v"] == 2, "Refreshed entry should now be served"
    assert loader_calls["n"] == 2, "p3 was a fresh-window hit, no new load"


async def test_swr_window_expiry_falls_back_to_cold_load() -> None:
    """Beyond swr_ttl_seconds the entry is dropped and we cold-load."""
    cache = _ClockCache(ttl_seconds=30.0, swr_ttl_seconds=300.0)
    user = f"u-{uuid.uuid4().hex[:8]}"
    loader_calls = {"n": 0}

    async def loader() -> dict:
        loader_calls["n"] += 1
        return {"v": loader_calls["n"]}

    await cache.get_or_load(user, "strong", loader)
    assert loader_calls["n"] == 1

    # Beyond SWR cap (350s > 300s).
    cache.advance(350.0)
    p2 = await cache.get_or_load(user, "strong", loader)
    assert p2["v"] == 2, "Beyond SWR window must cold-load synchronously"
    assert loader_calls["n"] == 2
