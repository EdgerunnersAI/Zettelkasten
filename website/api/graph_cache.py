"""WAVE-C 1c-A.3 — Per-user /api/graph cache + single-flight wrapper.

Implements locked decision **D-KG-6**: in-process LRU (cap=200), TTL=30s,
per-user lock to coalesce concurrent reads (single-flight), 20s upstream
timeout. Locked decision **D-KG-7**: full-invalidate on summarize / zettel
mutation.

Why single-flight: WAVE-B Phase 0 discovery flagged that the v2 / per-user
``/api/graph`` path has no thundering-herd protection — under bursty Strong/
Pro synth the upstream Supabase round-trip (`_v2_assemble_graph`) can take
seconds, and 50 concurrent loads from the same user historically all
re-executed it. The asyncio.Future coalescer collapses N concurrent in-flight
loads for the same key to one upstream call; everyone awaits the same Future.

Why per-user (not global) lock: a global lock would serialize traffic across
users, defeating the point. A per-user lock pinned by ``user_id`` lets two
different users still race in parallel.

Why bucketed cache key on ``min_strength``: the D-KG-3 render-threshold
filter creates 3 distinct response shapes (strong / medium / weak). Bucketing
collapses request variance ``min_strength=0.71`` and ``=0.83`` to the same
``"strong"`` key, sidestepping cache fragmentation while remaining
correctness-safe (server still applies the user's exact threshold post-load).

Anti-pattern guards:
- Pure in-process state — no Redis, no asyncio cross-loop hazards.
- LRU cap honored even under burst (eviction is O(1) via OrderedDict.popitem).
- Per-user inflight Future is cleared in a finally to prevent permanent
  poisoning if upstream raises.
- 20s wait_for is defensive against pathological asyncpg hangs (CLAUDE.md
  protected timeout knobs are 180s gunicorn / 240s caddy — this 20s sits
  below both, so an upstream that ignores it still surfaces a TimeoutError
  to the client well before the worker dies).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# ── Locked tunables (D-KG-6 / D-KG-7) ─────────────────────────────────────────

_CACHE_CAP: int = 200
_CACHE_TTL_SECONDS: float = 30.0
# SWR window: serve stale up to this many seconds AFTER fresh-TTL expires,
# while a background task recomputes. p95 cold-miss becomes <100ms (cached
# stale) + occasional background work — research-recommended pattern for
# expensive metric endpoints (web.dev SWR, Cloudflare/Fastly precedent).
_SWR_TTL_SECONDS: float = 300.0
_UPSTREAM_TIMEOUT_SECONDS: float = 20.0

__all__ = [
    "UserGraphCache",
    "bucket_for_strength",
    "get_default_cache",
]


def bucket_for_strength(min_strength: float | None) -> str:
    """Map a continuous threshold to one of three cache buckets.

    D-KG-3 render thresholds:
        strong  ≥ 0.7
        medium  0.4 ≤ x < 0.7
        weak    < 0.4
    None / 0.0 → "weak" (largest payload). Float boundary uses ≥ on the
    high side so 0.7 lands in "strong" deterministically.
    """
    if min_strength is None:
        return "weak"
    try:
        v = float(min_strength)
    except (TypeError, ValueError):
        return "weak"
    if v >= 0.7:
        return "strong"
    if v >= 0.4:
        return "medium"
    return "weak"


# ── Cache implementation ─────────────────────────────────────────────────────


class UserGraphCache:
    """Per-user LRU + single-flight graph cache.

    Public surface:
        await cache.get_or_load(user_id, bucket, loader)
            Returns the cached payload or invokes ``loader()`` once,
            coalescing concurrent calls for the same (user_id, bucket).

        cache.invalidate(user_id)
            Drop ALL bucket entries for the user (D-KG-7 full-invalidate
            on summarize/mutation). Cancels nothing; in-flight Futures are
            allowed to complete and their result is dropped post-resolution.

        cache.clear()
            Reset everything. Test/admin only.

    Concurrency: asyncio-only — there is no thread re-entry. The lock is
    an ``asyncio.Lock`` and all access is awaited.
    """

    def __init__(
        self,
        *,
        capacity: int = _CACHE_CAP,
        ttl_seconds: float = _CACHE_TTL_SECONDS,
        swr_ttl_seconds: float = _SWR_TTL_SECONDS,
        timeout_seconds: float = _UPSTREAM_TIMEOUT_SECONDS,
    ) -> None:
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._swr_ttl = swr_ttl_seconds
        self._timeout = timeout_seconds
        # Ordered for O(1) LRU eviction; key = (user_id, bucket).
        self._store: "OrderedDict[tuple[str, str], tuple[dict, float]]" = OrderedDict()
        # Inflight Futures for single-flight coalescing.
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}
        self._lock = asyncio.Lock()

    # ----- introspection (tests) ---------------------------------------

    def __len__(self) -> int:
        return len(self._store)

    def keys(self) -> list[tuple[str, str]]:
        return list(self._store.keys())

    def inflight_count(self) -> int:
        return len(self._inflight)

    # ----- internals ----------------------------------------------------

    def _now(self) -> float:
        return time.monotonic()

    def _expired(self, expires_at: float) -> bool:
        return expires_at <= self._now()

    def _evict_lru(self) -> None:
        """Pop oldest entry; called under lock when over capacity."""
        if len(self._store) <= self._capacity:
            return
        # popitem(last=False) → FIFO/LRU eviction.
        evicted_key, _ = self._store.popitem(last=False)
        logger.debug("graph_cache: evicted %s (cap=%d)", evicted_key, self._capacity)

    def _store_set(self, key: tuple[str, str], payload: dict) -> None:
        """Insert/refresh under lock; honour LRU cap."""
        # Move-to-end semantics so refresh = recency bump.
        self._store[key] = (payload, self._now() + self._ttl)
        self._store.move_to_end(key, last=True)
        self._evict_lru()

    # ----- public API --------------------------------------------------

    async def get_or_load(
        self,
        user_id: str,
        bucket: str,
        loader: Callable[[], Awaitable[dict]],
    ) -> dict:
        """Cache hit → return; miss → coalesce + load + cache + return.

        Single-flight semantics: if N tasks call this with the same key
        concurrently, only ONE invokes ``loader``; the other N-1 await the
        same Future and receive the same payload (no duplicate upstream
        round-trips, no extra DB load).
        """
        key = (str(user_id), str(bucket))

        # Phase 1: cache + inflight check under the lock.
        await self._lock.acquire()
        try:
            entry = self._store.get(key)
            if entry is not None:
                payload, expires_at = entry
                now = self._now()
                if now <= expires_at:
                    # FRESH: standard hit, no refresh needed.
                    self._store.move_to_end(key, last=True)
                    return payload
                # SWR window: stale but still serveable while we refresh in bg.
                stale_until = expires_at + (self._swr_ttl - self._ttl)
                if now <= stale_until:
                    # Serve stale immediately + kick off background refresh
                    # (idempotent: only one refresh in flight per key).
                    if key not in self._inflight:
                        refresh_future = asyncio.get_running_loop().create_future()
                        self._inflight[key] = refresh_future
                        # Schedule outside the lock; the task acquires it itself.
                        asyncio.create_task(
                            self._background_refresh(key, loader, refresh_future)
                        )
                    self._store.move_to_end(key, last=True)
                    return payload
                # Beyond SWR window: drop and fall through to cold single-flight.
                self._store.pop(key, None)

            existing = self._inflight.get(key)
            if existing is not None:
                # Coalesce: another task is already loading this key.
                future = existing
                self._lock.release()
                try:
                    return await future
                except Exception:
                    # Re-acquire so the finally pattern below stays consistent.
                    raise
                finally:
                    pass
            # We're the elected loader.
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._inflight[key] = future
        finally:
            if self._lock.locked():
                self._lock.release()

        # Phase 2: actually load (lock released so other users can race).
        try:
            payload = await asyncio.wait_for(loader(), timeout=self._timeout)
        except BaseException as exc:  # includes asyncio.TimeoutError + cancel
            # Resolve the Future BEFORE removing it from `_inflight` — both
            # under the same lock acquisition. If we popped first, a follower
            # entering between pop() and set_exception() would see neither a
            # cached entry NOR an inflight Future and would trigger its own
            # upstream load — N+1 storm under burst with upstream errors.
            async with self._lock:
                if not future.done():
                    future.set_exception(
                        exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                    )
                self._inflight.pop(key, None)
            raise

        # Phase 3: publish + resolve Future + clear inflight, all under lock.
        # Same ordering guarantee as the error path: a follower that grabs the
        # lock between operations sees either a cached entry OR an inflight
        # Future, never both empty.
        async with self._lock:
            self._store_set(key, payload)
            if not future.done():
                future.set_result(payload)
            self._inflight.pop(key, None)
        return payload

    async def _background_refresh(
        self,
        key: tuple[str, str],
        loader: Callable[[], Awaitable[dict]],
        future: asyncio.Future,
    ) -> None:
        """Background SWR refresh: recompute, publish, never bubble exceptions.

        Same lock-then-resolve-then-pop ordering as the cold path so a follower
        coalescing on the inflight Future during refresh sees consistent state.
        Failures are logged but never re-raised — stale entry remains served
        until the next request triggers another refresh attempt.
        """
        try:
            payload = await asyncio.wait_for(loader(), timeout=self._timeout)
        except BaseException as exc:
            async with self._lock:
                if not future.done():
                    future.set_exception(
                        exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                    )
                self._inflight.pop(key, None)
            logger.warning(
                "graph_cache: SWR background refresh failed for %s: %s", key, exc
            )
            return
        async with self._lock:
            self._store_set(key, payload)
            if not future.done():
                future.set_result(payload)
            self._inflight.pop(key, None)

    def invalidate(self, user_id: str) -> int:
        """Drop ALL bucket entries for ``user_id``. Returns count removed.

        Inflight Futures are NOT cancelled — they're allowed to complete and
        their result dropped (the next request will reload). This avoids
        racing followers seeing CancelledError mid-await.
        """
        target = str(user_id)
        # Synchronous: callers (POST /api/summarize, DELETE/PATCH /zettels)
        # are already in async handlers; we want this fire-and-forget cheap.
        keys = [k for k in self._store.keys() if k[0] == target]
        for k in keys:
            self._store.pop(k, None)
        logger.debug(
            "graph_cache: invalidated %s (%d entries)", target, len(keys)
        )
        return len(keys)

    def clear(self) -> None:
        """Reset everything. Test / admin only."""
        self._store.clear()
        self._inflight.clear()


# ── Module-singleton accessor ─────────────────────────────────────────────────

_DEFAULT_CACHE: UserGraphCache | None = None


def get_default_cache() -> UserGraphCache:
    """Lazily instantiate the process-singleton cache.

    Lazy so that test code which monkey-patches the constants (capacity, TTL)
    can do so before first use.
    """
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = UserGraphCache()
    return _DEFAULT_CACHE
