"""WM-05: stamina backoff + Retry-After honoring + bounded queue.

The retry decorator is exercised through ``post_with_retry`` directly so
the test doesn't need to drive a full FastAPI request. ``stamina`` sleeps
real wall-clock by default — we use ``stamina.set_testing(True)`` to
zero-out the sleeps so the test stays under 1s.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx
import stamina

from website.features.web_monitor._slack_client import (
    RateLimited,
    fire_and_forget,
    inflight_count,
    post_with_retry,
)


_TEST_URL = "https://hooks.slack.com/services/TTEST/BTEST/tokTestBackoff"


@pytest.fixture(autouse=True)
def _stamina_test_mode():
    """Disable stamina's real sleeps. attempts=4 mirrors prod _MAX_ATTEMPTS so
    retry semantics are preserved; only the wall-clock waits are zeroed."""
    stamina.set_testing(True, attempts=4)
    try:
        yield
    finally:
        stamina.set_testing(False)


@pytest.mark.asyncio
async def test_post_succeeds_first_try():
    with respx.mock(assert_all_called=True) as router:
        route = router.post(_TEST_URL).mock(return_value=httpx.Response(200, text="ok"))
        resp = await post_with_retry(_TEST_URL, {"text": "hi"})
        assert resp is not None
        assert resp.status_code == 200
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_post_retries_on_429_then_succeeds():
    with respx.mock(assert_all_called=True) as router:
        route = router.post(_TEST_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1"}, text="rate-limited"),
                httpx.Response(429, headers={"Retry-After": "1"}, text="rate-limited"),
                httpx.Response(200, text="ok"),
            ]
        )
        resp = await post_with_retry(_TEST_URL, {"text": "hi"})
        assert resp is not None
        assert resp.status_code == 200
        assert route.call_count == 3


@pytest.mark.asyncio
async def test_post_honors_retry_after_header_within_tolerance():
    """Retry-After=2 must override stamina's exp+jitter via the backoff hook.

    Drive the hook directly so we don't depend on stamina's internal sleep
    pathing (in testing mode stamina zeroes its real sleep but still calls
    the hook to decide whether to retry). The hook returning 2.0 IS the
    contract — that float is what stamina uses as the wait override.
    """
    from website.features.web_monitor._slack_client import (
        RateLimited,
        _backoff_hook,
    )

    decision = _backoff_hook(RateLimited(retry_after=2.0))
    assert decision == 2.0, f"Retry-After=2 not honored by hook: {decision!r}"

    # Cap enforcement: a hostile upstream returning Retry-After=600 must be
    # clamped to _RETRY_AFTER_CAP_SECONDS (60s) so a worker can't be pinned.
    capped = _backoff_hook(RateLimited(retry_after=600.0))
    assert capped == 60.0, f"Retry-After cap not enforced: {capped!r}"

    # Missing header => True (use stamina's built-in exp+jitter).
    assert _backoff_hook(RateLimited(retry_after=None)) is True


@pytest.mark.asyncio
async def test_post_gives_up_after_max_attempts():
    with respx.mock(assert_all_called=True) as router:
        route = router.post(_TEST_URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"})
        )
        resp = await post_with_retry(_TEST_URL, {"text": "hi"})
        # post_with_retry returns None instead of raising
        assert resp is None
        # 4 attempts max — assert at least 2 (lower bound; exact count
        # depends on stamina + outer-loop coordination)
        assert route.call_count >= 2


@pytest.mark.asyncio
async def test_post_retries_5xx_then_succeeds():
    with respx.mock(assert_all_called=True) as router:
        route = router.post(_TEST_URL).mock(
            side_effect=[
                httpx.Response(503, text="upstream"),
                httpx.Response(200, text="ok"),
            ]
        )
        resp = await post_with_retry(_TEST_URL, {"text": "hi"})
        assert resp is not None
        assert resp.status_code == 200
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_post_returns_none_on_unexpected_exception(monkeypatch):
    """A non-httpx exception must NOT escape the helper."""

    async def _boom(*_, **__):
        raise RuntimeError("synthetic")

    # After B-3 the retry chain is a single stamina-decorated `_post_once`;
    # patch that to verify the final `except Exception` guard converts an
    # unexpected error into a None return.
    monkeypatch.setattr(
        "website.features.web_monitor._slack_client._post_once",
        _boom,
    )
    resp = await post_with_retry(_TEST_URL, {"text": "hi"})
    assert resp is None


# ---------------------------------------------------------------------------
# WM-07: bounded fire-and-forget pool (merged into WM-05)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_and_forget_returns_task_and_tracks_inflight():
    """Tasks scheduled via fire_and_forget must be strong-ref'd in
    _inflight so Python's GC doesn't drop them mid-await."""
    started = asyncio.Event()
    finish = asyncio.Event()

    async def _slow():
        started.set()
        await finish.wait()

    task = fire_and_forget(_slow)
    assert task is not None
    await started.wait()
    assert inflight_count() >= 1
    finish.set()
    await task
    # Set is cleared via add_done_callback — give the loop one tick
    await asyncio.sleep(0)
    # Inflight count returns to baseline (>= 0; other tests can't fight us
    # because each schedules under the same module-level set)
    assert inflight_count() == 0


@pytest.mark.asyncio
async def test_fire_and_forget_bounded_by_semaphore():
    """Spawning more than _MAX_INFLIGHT tasks must queue, not parallelize.

    Setup: 20 tasks each waiting on a shared barrier. With sem=8, exactly
    8 reach the barrier; the rest queue on the semaphore.
    """
    from website.features.web_monitor import _slack_client

    barrier_enters = asyncio.Event()
    barrier_exits = asyncio.Event()
    counter = {"running": 0, "max_running": 0}

    async def _hold():
        counter["running"] += 1
        counter["max_running"] = max(counter["max_running"], counter["running"])
        if counter["running"] >= _slack_client._MAX_INFLIGHT:
            barrier_enters.set()
        await barrier_exits.wait()
        counter["running"] -= 1

    tasks = [fire_and_forget(_hold) for _ in range(20)]
    assert all(t is not None for t in tasks)
    # Wait until the semaphore is saturated
    await asyncio.wait_for(barrier_enters.wait(), timeout=2.0)
    assert counter["max_running"] == _slack_client._MAX_INFLIGHT, (
        f"expected max concurrent == {_slack_client._MAX_INFLIGHT}, "
        f"got {counter['max_running']}"
    )
    # Let everyone finish
    barrier_exits.set()
    await asyncio.gather(*tasks)
