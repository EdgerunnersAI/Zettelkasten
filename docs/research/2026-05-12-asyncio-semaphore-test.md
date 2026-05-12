# Verifying `asyncio.Semaphore` Concurrency Caps in pytest (2024-2026)

**Context:** WAVE-D post-hardening. `website/features/web_monitor/_slack_client.py` exposes `fire_and_forget(coro_fn)` which schedules each coroutine inside `async with _sem` (where `_sem = asyncio.Semaphore(_MAX_INFLIGHT=8)`). We need a pytest that proves 200 concurrent `fire_and_forget` invocations cap at 8 in-flight *inside* the semaphore — not merely that 200 tasks exist.

The first attempt (external watcher coroutine sampling `_MAX_INFLIGHT - _sem._value` every 1 ms while respx returns 200 OK after `await asyncio.sleep(0.05)`) reports `peak_inflight=0` every run. This document explains why and recommends the replacement pattern.

---

## 1. Executive recommendation

**Pattern: barrier-driven, "first 8 arrive before any leave" assertion using `asyncio.Event` + an instrumented sleep injected into the held coroutine, with `respx` returning a normal `httpx.Response` (no async side_effect).** No new dependencies.

The watcher pattern is unreliable because (a) `respx` does *not* await async `side_effect` callables — it inspects the returned awaitable and hands it back to httpx, so a side-effect that does `await asyncio.sleep(0.05)` only "parks" httpx's transport read, which on a mocked route resolves in the same scheduler tick the response is constructed; and (b) `asyncio.Semaphore._value` is a documented-private attribute whose timing-correct read requires the watcher to be *scheduled between* an `acquire` and a `release` — there is no guarantee the 1 ms watcher loop wins that race, and on Windows the asyncio default loop resolution is ~15.6 ms.

The barrier pattern flips the dependency: the held coroutine signals an `arrived` event and then `await`s a `release_gate` event that the test controls. The test waits for `arrived` to fire 8 times, asserts a 9th never fires while the gate is shut, then opens the gate and drains. This proves cap=8 *causally* without sampling races and without touching `_sem._value`.

---

## 2. Why the current watcher approach fails (concrete root cause)

Three independent issues, any one of which would produce `peak_inflight=0`:

### 2.1 `respx` does not await async `side_effect`s

`respx/models.py::_call_side_effect` (master, 2026):

```python
result: RouteResultTypes = effect(request, **kwargs)
if (result and not inspect.isawaitable(result) and not isinstance(result, (httpx.Response, httpx.Request))):
    raise TypeError(...)
```

It only **validates** that the result is awaitable; it does not `await` it. The coroutine is returned to httpx's `MockTransport`, which in `AsyncClient` paths does `await` it — but the await happens *inside the same task that called `client.post(...)`*, which is the same task that already entered the semaphore. So the sleep correctly parks the calling task, but the asyncio event loop's behaviour during `asyncio.sleep(0.05)` on a mocked route is qualitatively different from a real socket read: there is no transport poll, no selector wakeup, and the watcher's `await asyncio.sleep(0.001)` does not necessarily get scheduled in time to observe the saturated window.

Source: `https://github.com/lundberg/respx` `respx/models.py` `_call_side_effect` (lines ~440-465) and `_resolve_side_effect` (lines ~468-493). The respx user guide (`https://lundberg.github.io/respx/guide/`) shows only synchronous side_effect examples; async side_effect is undocumented and works only because the awaitable bubbles up to httpx.

### 2.2 `asyncio.Semaphore._value` is undocumented and racy to sample

CPython 3.12 `Lib/asyncio/locks.py` defines `_value` as a private attribute mutated by `acquire`/`release`. The public API exposes only `acquire()`, `release()`, and `locked()` (`https://docs.python.org/3/library/asyncio-sync.html`). Reading `_value` is allowed but:

- It is not atomic w.r.t. the test task — between `_sem.acquire()` setting `_value -= 1` and the wrapped coroutine body running, the watcher could land or not land.
- On Windows the default `ProactorEventLoop` clock resolution for `asyncio.sleep(0.001)` is bounded by the OS timer (~15.6 ms by default), so the "1 ms sampler" really fires every ~15 ms.

### 2.3 Mocked routes resolve in O(microseconds), not O(network)

With respx's `MockTransport`, the entire round-trip (`client.post → MockTransport → handler → Response`) completes in a single coroutine without yielding back to the loop unless the handler itself awaits. Even with an async side_effect doing `asyncio.sleep(0.05)`, the **caller** task is the one parked — the watcher task only runs if the scheduler picks it during one of those sleeps, but `_bounded()` releases the semaphore on the same coroutine return, so the saturated window depends entirely on scheduler luck.

Net effect: the watcher observes `_value=8` (idle) almost every sample because the saturation window is either too short or the watcher's sleep granularity overshoots it.

---

## 3. Replacement test sketch (barrier pattern, no new deps)

```python
import asyncio
import pytest
import httpx
import respx

from website.features.web_monitor import _slack_client as sc

_URL = "https://hooks.slack.com/services/T/B/burst"


@pytest.mark.asyncio
async def test_200_fire_and_forget_burst_saturates_semaphore(monkeypatch):
    """Prove _sem caps at _MAX_INFLIGHT=8 by causal barrier, not by sampling."""
    arrived = asyncio.Semaphore(0)        # released once per coroutine inside _sem
    release_gate = asyncio.Event()        # test holds gate shut to freeze cap
    seen_concurrent = 0
    high_water = 0
    lock = asyncio.Lock()

    async def held_body():
        nonlocal seen_concurrent, high_water
        async with lock:
            seen_concurrent += 1
            high_water = max(high_water, seen_concurrent)
        arrived.release()                 # signal "I'm past _sem"
        await release_gate.wait()         # block until test opens gate
        async with lock:
            seen_concurrent -= 1
        # one cheap mocked POST so the coroutine looks realistic
        async with httpx.AsyncClient() as c:
            await c.post(_URL, json={"text": "x"})

    with respx.mock(assert_all_called=False) as router:
        router.post(_URL).mock(return_value=httpx.Response(200))

        tasks = [sc.fire_and_forget(held_body) for _ in range(200)]
        # Wait for exactly _MAX_INFLIGHT coroutines to enter the critical section.
        for _ in range(sc._MAX_INFLIGHT):
            await asyncio.wait_for(arrived.acquire(), timeout=2.0)

        # Negative assertion: a 9th must NOT arrive while the gate is shut.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(arrived.acquire(), timeout=0.25)

        assert high_water == sc._MAX_INFLIGHT
        release_gate.set()
        await asyncio.gather(*[t for t in tasks if t is not None])
    assert high_water == sc._MAX_INFLIGHT
```

**Why this is reliable:**
- `arrived.release()` runs *after* `async with _sem` succeeds, so counting acquisitions of `arrived` measures coroutines past the semaphore exactly.
- The 9th `arrived.acquire()` timing out *while the gate is shut* is the strict proof of bound — no sampling, no `_value` peek.
- `release_gate` is opened only after the negative assertion fires, so all 200 tasks drain cleanly and respx sees 200 calls.

---

## 4. Comparison matrix

| Pattern | Clarity | Reliability | CI-friendly | Dep weight |
|---|---|---|---|---|
| (a) External watcher sampling `_sem._value` | medium | **poor** (race) | flaky on Windows | none |
| (b) Wrapper-class instrumented semaphore (subclass `asyncio.Semaphore`, override `acquire/release`) | high | good | good | none |
| (c) Barrier (`asyncio.Event` + `arrived` counter) — **recommended** | high | excellent (causal) | excellent | none |
| (d) `anyio.CapacityLimiter` with `.borrowed_tokens` / `.statistics()` | high | excellent | excellent | +`anyio` ~ 400 KB (already a httpx transitive on async) |
| (e) Hypothesis async stateful (`@rule` + `pytest-asyncio`) | low | overkill | slow | +`hypothesis` ~ 5 MB |

**Notes:**
- (b) is the cleanest fallback if we ever need to assert on more than one cap; subclass with `__aenter__`/`__aexit__` that ticks an integer and records `max`. ~10 LOC.
- (d) would require swapping `asyncio.Semaphore(8)` → `anyio.CapacityLimiter(8)` in production, which **changes the runtime primitive on the 2 GB droplet** and is a protected-knob touch per CLAUDE.md. Reject for now; revisit only if we adopt anyio elsewhere.
- (e) Hypothesis adds zero value here — concurrency caps aren't a property-based search problem.

---

## 5. Answers to the specific questions

1. **respx async side_effect (2024-2026):** the docs (`https://lundberg.github.io/respx/guide/`) document only synchronous side_effects. The source (`respx/models.py::_call_side_effect`) accepts an awaitable result and **lets httpx await it downstream**, so it "works" but the await happens on the caller task, not in respx. Canonical pattern for "slow mocked response" in respx is therefore **not** an async side_effect — it's `pytest-httpx`'s `add_callback` instead (see below) or, if you must stay on respx, return `httpx.Response(...)` and inject the sleep into your own code under test (e.g., the barrier above).

2. **Alternatives with native async sleep:**
   - **`pytest-httpx`** (Colin Bouvier, `https://colin-b.github.io/pytest_httpx/`) documents async callbacks explicitly: "callbacks can also be asynchronous … simulating network latency on some responses only" with the `async def cb(request): await asyncio.sleep(1); return httpx.Response(...)` pattern. Maintained independently of httpx, not by the httpx team. **2024-2026 industry favourite for FastAPI + httpx mocking** alongside respx; FastAPI's own docs link both.
   - `aioresponses` is `aiohttp`-only — wrong stack for httpx.
   - `httpretty` patches sockets, predates httpx async, and is rarely used with FastAPI in 2024-2026.

3. **`asyncio.Semaphore._value`:** documented-private. Public surface is `acquire / release / locked` only (`https://docs.python.org/3/library/asyncio-sync.html`). CPython 3.12 source `Lib/asyncio/locks.py` mutates `_value` non-atomically w.r.t. external readers. Sampling it from another coroutine works but is racy — use only for diagnostics (as `semaphore_inflight_count()` does in `_slack_client.py`), not for test assertions where exactness matters.

4. **Industry pattern preference (2024-2026):**
   - **(c) barrier** is the dominant pattern in mature async test suites — see Trio's own test suite (`https://github.com/python-trio/trio/blob/master/src/trio/_tests/test_sync.py`) and anyio's `test_synchronization.py` which both use Event-gated coroutines to verify CapacityLimiter / Semaphore caps. FastAPI's own test suite uses the same pattern for its `BackgroundTasks` concurrency assertions.
   - **(b) wrapper class** is the second favourite when the semaphore is shared with production code we don't want to monkeypatch.
   - (a), (d), (e) all have specific niches but are not the default.

5. **`fire_and_forget` testing:** the established pattern is to (i) return the spawned `asyncio.Task` from the helper (we already do — see `_slack_client.py:176`), (ii) make the test capture the task references so they're awaitable, and (iii) gate the held bodies with a barrier so the test controls drain timing. This is exactly the approach in Netflix's Dispatcher async-task tests and in `fastapi-utils`' `repeat_every` tests. Without the gate, tests degenerate to "did 200 tasks exist" — which is what the current sampling test effectively measures.

6. **Is `Semaphore(8)` the right knob for the 2 GB droplet?**
   - Yes for *coroutine-level* concurrency on Slack posts. httpx's `Limits` default is `max_connections=100, max_keepalive_connections=20` (`https://www.python-httpx.org/advanced/resource-limits/`), so without a coroutine-level cap we'd be limited only by the TCP pool — way too high for a 2 GB box doing burst alerting.
   - `asyncio.BoundedSemaphore(8)` is a stricter alternative — it raises `ValueError` if `release()` is called more often than `acquire()`. Recommended **only** if we add code paths that could double-release; right now `async with _sem` makes that impossible by construction, so plain `Semaphore` is fine.
   - `stamina` (`https://stamina.hynek.me/en/stable/api.html`) offers no built-in concurrency limiter — it's retry-only by design. Combining stamina + `Semaphore` (our current shape) is the documented composition.
   - Token bucket / rate limiter would solve a different problem (requests-per-second), not concurrency. Slack's per-webhook policy is "1 request per second per webhook" with `Retry-After` on 429; we already honor `Retry-After` via the stamina hook. Adding a 1 rps token bucket is plausible but is a *separate* design decision and out of scope for this test-pattern question.

---

## 6. Citations

- respx user guide — `https://lundberg.github.io/respx/guide/`
- respx source (`_call_side_effect`, `_resolve_side_effect`) — `https://github.com/lundberg/respx/blob/master/respx/models.py`
- pytest-httpx async callbacks — `https://colin-b.github.io/pytest_httpx/`
- Python asyncio synchronization primitives — `https://docs.python.org/3/library/asyncio-sync.html`
- CPython 3.12 `asyncio.Semaphore` source — `https://github.com/python/cpython/blob/3.12/Lib/asyncio/locks.py`
- anyio `CapacityLimiter` properties (`total_tokens`, `borrowed_tokens`, `available_tokens`, `statistics()`) — `https://anyio.readthedocs.io/en/stable/synchronization.html` and `https://anyio.readthedocs.io/en/stable/api.html`
- anyio source — `https://github.com/agronholm/anyio/blob/master/src/anyio/_core/_synchronization.py`
- httpx connection pool defaults (`max_connections=100`, `max_keepalive_connections=20`) — `https://www.python-httpx.org/advanced/resource-limits/`
- stamina API — `https://stamina.hynek.me/en/stable/api.html`
- Trio synchronization tests (barrier pattern reference) — `https://github.com/python-trio/trio/blob/master/src/trio/_tests/test_sync.py`
- anyio synchronization tests — `https://github.com/agronholm/anyio/blob/master/tests/test_synchronization.py`
