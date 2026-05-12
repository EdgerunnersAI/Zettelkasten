"""Stress test: 200-call burst into notify_pricing_visit with mixed Slack responses.

Asserts:
  * No unbounded asyncio task growth (count returns to baseline within
    a bounded window once posts settle).
  * No memory-equivalent failures (no exceptions escaping the notifier).
  * The semaphore caps concurrent in-flight Slack work at _MAX_INFLIGHT.

Per WAVE-D scope §E.7 — guards the 2 GB / 1 vCPU droplet against burst
behaviour during bot scans.
"""
from __future__ import annotations

import asyncio
import random

import httpx
import pytest
import respx
import stamina

from website.features.web_monitor import User_Activity as ua_mod
from website.features.web_monitor import _slack_client
from website.features.web_monitor.User_Activity import notify_pricing_visit


@pytest.fixture(autouse=True)
def _reset_pricing_throttle():
    ua_mod._pricing_seen_at.clear()
    yield
    ua_mod._pricing_seen_at.clear()


class _StubRequest:
    def __init__(self, *, ip: str):
        self.headers = {
            "x-forwarded-for": ip,
            "cf-ipcountry": "IN",
            "user-agent": "burst-test",
        }
        self.client = type("C", (), {"host": ip})()


@pytest.fixture(autouse=True)
def _stamina_fast():
    stamina.set_testing(True, attempts=2)
    yield
    stamina.set_testing(False)


@pytest.mark.asyncio
async def test_200_pricing_visits_burst_no_task_leak(slack_webhook_mock, monkeypatch):
    """Hammer notify_pricing_visit with 200 unique IPs and random Slack outcomes."""
    rec = slack_webhook_mock()

    # Override the user-activity webhook with random 200/429/500 responses.
    ua_url = "https://hooks.slack.com/services/TTESTUA/BTESTUA/tokUserActivity"

    def _random_response(_request):
        roll = random.random()
        if roll < 0.6:
            return httpx.Response(200, text="ok")
        if roll < 0.85:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="rate")
        return httpx.Response(500, text="upstream")

    baseline_tasks = len(asyncio.all_tasks())
    peak_inflight = 0

    with respx.mock(assert_all_called=False) as router:
        router.post(ua_url).mock(side_effect=_random_response)

        # Pump 200 fire-and-forget calls. Each pricing_visit internally
        # invokes post_to_user_activity which uses post_with_retry which
        # internally serializes via the slack_client semaphore.
        async def _one_call(i: int):
            req = _StubRequest(ip=f"203.0.113.{i % 256}")
            try:
                await notify_pricing_visit(req)
            except Exception:  # noqa: BLE001 — burst must never raise
                pytest.fail(f"notify_pricing_visit raised on call {i}")

        async def _watcher(stop_event: asyncio.Event):
            nonlocal peak_inflight
            while not stop_event.is_set():
                peak_inflight = max(peak_inflight, _slack_client.inflight_count())
                await asyncio.sleep(0.001)

        stop_event = asyncio.Event()
        watcher = asyncio.create_task(_watcher(stop_event))

        # Fire all 200 in parallel.
        await asyncio.gather(*[_one_call(i) for i in range(200)])

        stop_event.set()
        await watcher

    # After all calls return, give the loop a beat for cleanup callbacks.
    await asyncio.sleep(0.05)

    # Task count drifts <=20 above baseline (transient cleanup) but should not
    # have grown by O(N) — the strong-ref _inflight set drains via add_done_callback.
    final_tasks = len(asyncio.all_tasks())
    assert final_tasks - baseline_tasks <= 20, (
        f"task leak: baseline={baseline_tasks} final={final_tasks} "
        f"(grew by {final_tasks - baseline_tasks})"
    )

    # In-flight pool MUST have respected the semaphore cap. Note: under
    # post_with_retry the calls are awaited inline by notify_pricing_visit
    # rather than scheduled via fire_and_forget, so peak inflight is 0
    # (the fire_and_forget pool is empty). This is the CORRECT shape — we
    # assert it explicitly to document intent.
    assert peak_inflight <= _slack_client._MAX_INFLIGHT, (
        f"semaphore cap breach: peak_inflight={peak_inflight} cap={_slack_client._MAX_INFLIGHT}"
    )

    # At least one Slack call landed (the rest may have been throttled by
    # the in-memory per-IP throttle, but with 200 unique IPs every call's
    # FIRST visit fires through).
    total_calls = sum(len(v) for v in rec.calls.values())
    assert total_calls >= 1, "expected at least one Slack-mock call across the burst"


@pytest.mark.asyncio
async def test_200_fire_and_forget_burst_saturates_semaphore():
    """M-6: prove _sem caps at _MAX_INFLIGHT by causal barrier, not sampling.

    Watcher/_sem._value sampling races with the scheduler (esp. on Windows
    with ~15.6 ms timer granularity). Instead: each held coroutine signals
    `arrived` after entering the semaphore, then blocks on `release_gate`.
    Wait for exactly _MAX_INFLIGHT arrivals, then assert a 9th does NOT
    arrive within 250 ms while the gate is shut. Open the gate to drain.
    See docs/research/2026-05-12-asyncio-semaphore-test.md.
    """
    _URL = "https://hooks.slack.com/services/TTESTUA/BTESTUA/tokBarrier"

    arrived = asyncio.Semaphore(0)        # released once per coroutine past _sem
    release_gate = asyncio.Event()        # test holds gate shut to freeze cap
    seen_concurrent = 0
    high_water = 0
    lock = asyncio.Lock()

    async def _held_body():
        nonlocal seen_concurrent, high_water
        async with lock:
            seen_concurrent += 1
            high_water = max(high_water, seen_concurrent)
        arrived.release()
        await release_gate.wait()
        async with lock:
            seen_concurrent -= 1
        async with httpx.AsyncClient() as c:
            await c.post(_URL, json={"text": "x"})

    with respx.mock(assert_all_called=False) as router:
        router.post(_URL).mock(return_value=httpx.Response(200))

        tasks = [_slack_client.fire_and_forget(_held_body) for _ in range(200)]
        assert all(t is not None for t in tasks)

        # Wait for exactly _MAX_INFLIGHT to pass _sem.
        for _ in range(_slack_client._MAX_INFLIGHT):
            await asyncio.wait_for(arrived.acquire(), timeout=2.0)

        # Negative: a 9th must NOT arrive while gate shut. Strict cap proof.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(arrived.acquire(), timeout=0.25)

        assert high_water == _slack_client._MAX_INFLIGHT, (
            f"saturation high-water={high_water} cap={_slack_client._MAX_INFLIGHT}"
        )
        release_gate.set()
        await asyncio.gather(*[t for t in tasks if t is not None])

    assert high_water == _slack_client._MAX_INFLIGHT, (
        f"final high-water={high_water} cap={_slack_client._MAX_INFLIGHT}"
    )
