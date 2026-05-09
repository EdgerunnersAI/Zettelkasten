"""iter-12 Class P: event-loop lag sentinel.

Measures actual vs expected sleep interval each tick; p95 < 50 ms is the
Phase-2 gate for anchor-boost re-enable.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import TypedDict


class LagSnapshot(TypedDict):
    p50_ms: float
    p95_ms: float
    max_ms: float
    n: int


class EventLoopMonitor:
    """Coroutine-based canary that measures event-loop scheduling lag."""

    def __init__(self, interval_ms: float = 100, window: int = 600) -> None:
        self._interval = interval_ms / 1000.0  # convert to seconds
        self._window = window  # max samples retained
        self._samples: deque[float] = deque(maxlen=window)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Schedule the background tick task."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="event_loop_monitor")

    async def stop(self) -> None:
        """Cancel the tick task; idempotent."""
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            t0 = time.perf_counter()
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                return
            elapsed = time.perf_counter() - t0
            lag = max(0.0, (elapsed - self._interval) * 1000.0)  # ms, clamped
            self._samples.append(lag)

    def snapshot(self) -> LagSnapshot:
        """Return current lag percentiles over the rolling window."""
        if not self._samples:
            return {"p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "n": 0}
        sorted_samples = sorted(self._samples)
        n = len(sorted_samples)

        def pct(p: float) -> float:
            idx = max(0, int(p * n) - 1)
            return round(sorted_samples[idx], 3)

        return {
            "p50_ms": pct(0.50),
            "p95_ms": pct(0.95),
            "max_ms": round(sorted_samples[-1], 3),
            "n": n,
        }
