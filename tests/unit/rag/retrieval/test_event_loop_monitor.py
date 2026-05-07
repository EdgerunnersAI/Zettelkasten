"""iter-12 Class P validation: event-loop lag sentinel coroutine."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_lag_below_50ms_under_normal_load():
    from website.features.rag_pipeline.observability.event_loop_monitor import (
        EventLoopMonitor,
    )
    monitor = EventLoopMonitor(interval_ms=50)
    await monitor.start()
    await asyncio.sleep(0.5)  # let it tick ~10 times
    snapshot = monitor.snapshot()
    await monitor.stop()
    assert snapshot["p95_ms"] < 50.0
    assert snapshot["n"] >= 5


@pytest.mark.asyncio
async def test_detects_blocking_load():
    """A 200ms blocking sleep must make p95 lag spike above 100ms."""
    import time
    from website.features.rag_pipeline.observability.event_loop_monitor import (
        EventLoopMonitor,
    )
    monitor = EventLoopMonitor(interval_ms=20)
    await monitor.start()
    await asyncio.sleep(0.05)  # baseline ticks
    time.sleep(0.2)            # block the loop intentionally
    await asyncio.sleep(0.3)   # let post-block ticks land
    snapshot = monitor.snapshot()
    await monitor.stop()
    assert snapshot["max_ms"] > 100.0


@pytest.mark.asyncio
async def test_snapshot_empty_before_start():
    from website.features.rag_pipeline.observability.event_loop_monitor import (
        EventLoopMonitor,
    )
    monitor = EventLoopMonitor()
    snapshot = monitor.snapshot()
    assert snapshot == {"p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "n": 0}


@pytest.mark.asyncio
async def test_stop_cancels_cleanly():
    from website.features.rag_pipeline.observability.event_loop_monitor import (
        EventLoopMonitor,
    )
    monitor = EventLoopMonitor(interval_ms=50)
    await monitor.start()
    await asyncio.sleep(0.1)
    await monitor.stop()
    # Calling stop again should be a no-op
    await monitor.stop()
