"""Per-URL LRU cache for ``DenseVerifyResult`` payloads.

Enables the 3-call engine to amortize DenseVerify across repeated requests for
the same URL (web UI reloads, batch pipelines that re-enter, retries after a
transient downstream failure). The cache is purely additive: callers that do
NOT use it pay no cost, and a cache miss falls through to a fresh DV call.

Thread-safety: instances are safe for concurrent ``get_or_compute`` callers
— the class locks on the internal map and computes outside the lock to
avoid contention on long-running Gemini calls.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Generic, Optional, TypeVar

T = TypeVar("T")

_DEFAULT_MAXSIZE = 500
_DEFAULT_TTL_SECONDS = 86_400  # 24h


class LRUCache(Generic[T]):
    """Minimal LRU cache with per-entry TTL and a global capacity bound.

    ``maxsize`` evicts the least-recently-used entry on insert overflow.
    ``ttl_seconds`` silently drops expired entries on lookup (lazy TTL).
    Both defaults match the DenseVerify use case. A ``clock`` injection
    hook keeps tests deterministic.
    """

    def __init__(
        self,
        maxsize: int = _DEFAULT_MAXSIZE,
        ttl: int = _DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self._maxsize = maxsize
        self._ttl = ttl
        self._clock = clock
        self._store: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at <= self._clock()

    async def get(self, key: str) -> Optional[T]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._is_expired(expires_at):
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            expires_at = self._clock() + self._ttl
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (expires_at, value)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    async def get_or_compute(
        self,
        key: str,
        compute: Callable[[], Awaitable[T]],
    ) -> T:
        cached = await self.get(key)
        if cached is not None:
            return cached
        # Compute OUTSIDE the lock so two concurrent misses don't
        # serialize the upstream call. The extra cost is at most one
        # redundant compute on thundering-herd startup — acceptable
        # vs. locking every Gemini round-trip.
        value = await compute()
        await self.set(key, value)
        return value

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


def cache_key_for_url(url: str) -> str:
    """Stable sha1-hex digest of a normalized URL used as the cache key.

    Hashing keeps the cache map small when URLs include long query strings,
    and avoids URL-encoding mismatches between callers that have different
    normalization stages upstream.
    """
    normalized = (url or "").strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


__all__ = ["LRUCache", "cache_key_for_url"]
