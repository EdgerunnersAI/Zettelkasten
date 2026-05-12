# Slack Webhook Backoff & Circuit-Breaker — Library Selection (WAVE-D Phase 0, D-1)

**Date:** 2026-05-12
**Scope:** Pick the right Python async stack for retrying / rate-limiting / circuit-breaking the three Slack incoming-webhook calls in `website/features/web_monitor/`. Research-only; no code changes.
**Constraints:** FastAPI + httpx AsyncClient on a 2 GB / 1 vCPU DigitalOcean droplet, 2 gunicorn workers with `--preload`. Calls are already fire-and-forget via `asyncio.create_task`. Retry coroutine must not pin a worker; added wheels must be small.

---

## 1. Recommendation (one sentence)

**Use [`stamina` 26.1.0](https://pypi.org/project/stamina/) (released 2026-04-13, wraps [`tenacity` 9.1.4](https://pypi.org/project/tenacity/)) with a custom `wait` that reads `Retry-After` on 429, fronted by a project-local `asyncio.Semaphore`-bounded task pool that strong-refs in-flight Slack tasks** — this gives async-native exponential backoff with jitter, Slack-compliant `Retry-After` honoring, ~zero ceremony, production heritage (Hynek), and avoids the unbounded `create_task` failure mode without adding a circuit-breaker dependency that we don't yet need at our request volume.

Add `pybreaker` 1.4.1 **only if** a future post-mortem shows we're still hammering Slack after backoff exhaustion (deferred — flag as Phase-1 hardening, not Phase-0).

---

## 2. Comparison matrix (5 candidates × 7 criteria)

| Criterion | tenacity 9.1.4 | **stamina 26.1.0 (rec)** | httpx-retries 0.5.0 | pybreaker 1.4.1 / purgatory 3.0.1 | DIY semaphore + token bucket |
|---|---|---|---|---|---|
| **Last release** | 2026-02-07 ([PyPI](https://pypi.org/project/tenacity/)) | 2026-04-13 ([PyPI](https://pypi.org/project/stamina/)) | 2026-04-20 ([PyPI](https://pypi.org/project/httpx-retries/)) | pybreaker 2025-09-21 / purgatory 2024-11-02 ([GH](https://github.com/mardiros/purgatory)) | n/a |
| **Stale?** | No | No | No | pybreaker: borderline (>7mo); purgatory: stale (18mo) | n/a |
| **Async-FastAPI fit** | `AsyncRetrying` native ([docs](https://tenacity.readthedocs.io/en/latest/api.html)) | `@stamina.retry` works on async callables, identical sync/async API ([tutorial](https://stamina.hynek.me/en/stable/tutorial.html)) | `RetryTransport` plugs into `httpx.AsyncClient(transport=…)` ([docs](https://will-ockmore.github.io/httpx-retries/)) | pybreaker: asyncio only via undocumented coroutine path, primary doc still cites Tornado ([GH](https://github.com/danielfm/pybreaker)); purgatory: native `AsyncCircuitBreakerFactory` ([GH](https://github.com/mardiros/purgatory)) | Native asyncio primitives, no surprises |
| **Honors `Retry-After` on 429** | Not built-in — needs custom `wait_retry_after` wrapper (~15 lines, [Zeitbach 2024](https://zeitbach.com/blog/2024/08/15/honoring-the-retry-after-header-with-tenacity), [alexwlchan ref impl](https://github.com/alexwlchan/handling-http-429-with-tenacity)) | Same wrapper applies (stamina is a tenacity wrapper) — `on=` accepts a callable that can read response | **Yes, by default** — `Retry(respect_retry_after_header=True)`, retries `{429, 502, 503, 504}` via `parse_retry_after()` + `asleep()` ([source](https://github.com/will-ockmore/httpx-retries/blob/main/httpx_retries/retry.py)) | No — circuit-breakers fail fast on threshold; orthogonal to `Retry-After` | Manual — read header, `await asyncio.sleep(int(r.headers["retry-after"]))` |
| **Jitter (anti-thundering-herd)** | `wait_random_exponential` or `wait_exponential + wait_random` | Built-in exponential + jitter by default ([README](https://github.com/hynek/stamina)) | `backoff_factor` exponential, jitter optional via `Retry` config | n/a (breaker, not retry) | DIY — must remember to add |
| **Memory footprint** | tenacity wheel ~30 KB, pure-Python, no C deps | tenacity + stamina shim (~40 KB total) | tenacity-free, ~25 KB, depends only on httpx (already in tree) | pybreaker pure-Python ~20 KB; purgatory ~30 KB | Zero (stdlib) |
| **Production usage in 2024-2026 OSS** | Used by Airflow, OpenAI SDK, LangChain, Instructor ([example](https://python.useinstructor.com/concepts/retrying/)); de-facto std | Used by Hynek's own `attrs`/`structlog` ecosystem; growing in FastAPI shops since 24.x — author is core Python infra figure | Adopted by smaller httpx-centric projects; replaces deprecated `httpx-retry` (last release abandoned 2025-04-23) | pybreaker: Netflix-pattern reference impl, used in microservice case studies ([blog](https://thebackenddevelopers.substack.com/p/implementing-the-circuit-breaker)); purgatory: used by Blacksmith ([GH](https://github.com/mardiros/purgatory)) | n/a |
| **`respx` test ergonomics** | Stub 429 + Retry-After header, drive retries via mocked `httpx.AsyncClient`; well-documented pattern ([respx guide](https://lundberg.github.io/respx/guide/)) | Identical to tenacity (same underlying calls) + `stamina.set_testing()` context manager disables sleeps in tests | Excellent — transport-level, just mount mocked transport | Forces you to assert circuit state separately from response; awkward in `respx` | Hand-rolled — write own fakes |
| **Slack-webhook-specific fit** | Generic | Generic + Hynek's "do the right thing by default" — good fit since Slack 429 handling is a standard pattern | Tailored to httpx; works but the transport applies to **all** requests on that client (need a dedicated `AsyncClient` per webhook to avoid retry-ing unrelated calls) | Orthogonal — protects downstream from us, not us from downstream backpressure | Full control |

**Stale-library flags:** purgatory's last release (2024-11-02) is ~18 months old; per CLAUDE.md research discipline, this is a yellow flag — usable but won't pick up new asyncio fixes without a fork. pybreaker 1.4.1 (2025-09-21) is ~8 months old and the README still primarily advertises Tornado, so native asyncio is a second-class path.

---

## 3. Recommended code sketch (≤30 lines)

Wires `stamina` + custom `Retry-After`-aware wait + bounded task pool into the `post_to_user_activity` style. Drop into `website/features/web_monitor/_slack_client.py` (new shared module) so all three webhooks reuse it.

```python
# website/features/web_monitor/_slack_client.py
import asyncio, httpx, stamina
from tenacity import RetryCallState
_MAX_INFLIGHT = 16                                          # bounded fire-and-forget pool
_sem = asyncio.Semaphore(_MAX_INFLIGHT)
_inflight: set[asyncio.Task] = set()                        # strong-ref so GC doesn't drop tasks

def _wait_retry_after(state: RetryCallState) -> float:      # stamina passes RetryCallState through
    resp = getattr(state.outcome, "_result", None)
    hdr = getattr(resp, "headers", {}).get("retry-after") if resp else None
    if hdr:
        try: return min(float(hdr), 60.0)                   # cap at 60s; Slack rarely exceeds
        except ValueError: pass
    return min(2 ** state.attempt_number, 30.0)             # exp backoff fallback, capped

@stamina.retry(on=httpx.HTTPError, attempts=4, wait_initial=1.0, wait_jitter=2.0, wait_max=30.0)
async def _post(url: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, json=payload)
    if r.status_code == 429 or 500 <= r.status_code < 600:
        r.raise_for_status()                                # triggers stamina retry
    return r

def fire_and_forget(url: str, payload: dict) -> None:
    async def _bounded():
        async with _sem:                                    # cap concurrent webhook calls
            try: await _post(url, payload)
            except Exception: pass                          # never propagate; logged inside _post
    t = asyncio.create_task(_bounded())
    _inflight.add(t); t.add_done_callback(_inflight.discard)
```

**Why this shape:**
- `stamina.retry` gives jitter + max-attempts; the custom `_wait_retry_after` isn't wired here because stamina's public `wait_*` knobs already give exp+jitter; for `Retry-After` honoring we'd extend by raising a typed `RateLimited(retry_after=...)` exception inside `_post` and use `on=` with a `wait` callable — see [stamina docs on custom wait](https://stamina.hynek.me/en/stable/tutorial.html). (Full Retry-After integration is ~5 more lines; trimmed to keep sketch ≤30.)
- `_sem` + `_inflight` set together close two known footguns: unbounded `create_task` ([Python asyncio docs warn explicitly](https://docs.python.org/3/library/asyncio-task.html) that tasks without strong refs may be GC'd mid-flight) and unbounded concurrency under a 429 storm ([SuperFastPython semaphore guide](https://superfastpython.com/asyncio-semaphore/)).
- One `AsyncClient` per call is wasteful but matches current pattern — defer the per-webhook persistent client to a separate refactor task.

---

## 4. Test sketch (respx, forced 429 + Retry-After)

```python
# tests/unit/web_monitor/test_slack_backoff.py
import httpx, pytest, respx
from website.features.web_monitor._slack_client import _post

@pytest.mark.asyncio
@respx.mock
async def test_post_honors_retry_after_then_succeeds():
    url = "https://hooks.slack.com/services/T/B/X"
    route = respx.post(url).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"ok": True}),
    ])
    r = await _post(url, {"text": "hi"})
    assert r.status_code == 200
    assert route.call_count == 3

@pytest.mark.asyncio
@respx.mock
async def test_post_gives_up_after_max_attempts():
    url = "https://hooks.slack.com/services/T/B/X"
    respx.post(url).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    with pytest.raises(httpx.HTTPStatusError):
        await _post(url, {"text": "hi"})  # 4 attempts → still 429 → bubbles
```

Wrap the suite with `stamina.set_testing(True)` in a fixture to disable real sleeps during tests ([instrumentation docs](https://github.com/hynek/stamina/blob/main/docs/instrumentation.md)). Use `respx`'s `side_effect` list to script the 429-then-200 sequence ([respx examples](https://lundberg.github.io/respx/examples/)).

---

## 5. Why not the alternatives (one-line each)

- **Pure tenacity:** Same engine as stamina but without the opinionated defaults — you'd reinvent jitter/cap defaults that stamina already gets right ([Hynek release notes](https://github.com/hynek/stamina/blob/main/CHANGELOG.md)). Pick this only if we already used tenacity elsewhere; we don't.
- **httpx-retries:** Cleanest "just works for `Retry-After`" but applies at the transport level, so it retries **every** request on that client — fine if we keep one client per webhook URL, but our current code re-creates `AsyncClient` per call. Switching means a bigger refactor (persistent clients), and we lose the bounded-pool ergonomics in the same module. Keep as fallback if stamina+custom-wait becomes a maintenance burden.
- **pybreaker / purgatory:** Solves a different problem — fail-fast after N failures, not graceful retry. With 3 webhook channels at very low QPS (<1/s under any realistic load), the circuit-breaker rarely opens; adds operational complexity (state, half-open transitions) for marginal benefit. Reconsider in WAVE-E if Slack outages cause cascading failure in the request handler. Purgatory specifically: stale (18 months), red-flag per CLAUDE.md.
- **DIY semaphore + token-bucket:** Zero deps, but reinvents jitter, attempt counting, and `Retry-After` parsing — and the bug surface for retry logic is non-trivial (off-by-one on attempts, jitter formula, exception filtering). The bounded-pool *part* of DIY is still in the recommendation; only the retry decorator is delegated to stamina.

---

## 6. Anti-pattern confirmations (cited)

- **Unbounded `asyncio.create_task`:** Confirmed footgun — Python asyncio docs explicitly warn tasks need strong references or they may be GC'd mid-flight ([CPython 3.12+ asyncio.Task docs](https://docs.python.org/3/library/asyncio-task.html)). Mitigation: `set.add()` + `add_done_callback(set.discard)` pattern (in sketch above).
- **`time.sleep` in async:** Blocks the entire event loop; in a 2-worker preload deploy this would pin one of the two workers for the whole backoff duration. Always `await asyncio.sleep()` (stamina/tenacity do this internally).
- **Naive exp backoff without jitter:** Multi-worker (2 gunicorn workers × N requests in flight) will retry in lockstep after a Slack 429, doubling the burst on every attempt — classic thundering-herd. Stamina applies jitter by default; if rolling your own, AWS's "Full Jitter" formula (`sleep = random_between(0, base * 2^attempt)`) is the standard ([AWS Architecture Blog on exponential backoff and jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) — older but still canonical).

---

## 7. Risk & residual

- **`stamina` is opinionated** — if Slack ever returns a non-Retry-After 429 with a custom error body we want to special-case (e.g., webhook-disabled vs rate-limited), we'd extend the `on=` predicate. Trivial.
- **Circuit-breaker deferred** — if a Slack regional outage causes our retry loop to consume worker time for ~2 min × 16 concurrent tasks × 4 attempts = 128 worker-seconds, we may want pybreaker to short-circuit after N consecutive failures. Track as WAVE-D hardening item; do not bundle into this iteration without explicit approval (CLAUDE.md "beyond-plan = new decision").
- **One `AsyncClient` per call** is wasteful — known issue, not introduced by this change. Address in a follow-up refactor when we move web_monitor to a singleton client.

---

## Sources

- [Slack — Rate limits (Web API / incoming webhooks)](https://docs.slack.dev/apis/web-api/rate-limits/) — official, current 2024+ URL
- [Slack — Rate Limits (legacy api.slack.com)](https://api.slack.com/docs/rate-limits) — confirms `Retry-After` header behavior
- [tenacity 9.1.4 on PyPI (2026-02-07)](https://pypi.org/project/tenacity/)
- [tenacity API reference](https://tenacity.readthedocs.io/en/latest/api.html)
- [stamina 26.1.0 on PyPI (2026-04-13)](https://pypi.org/project/stamina/)
- [stamina tutorial (HTTP async example)](https://stamina.hynek.me/en/stable/tutorial.html)
- [stamina CHANGELOG](https://github.com/hynek/stamina/blob/main/CHANGELOG.md)
- [stamina instrumentation / testing docs](https://github.com/hynek/stamina/blob/main/docs/instrumentation.md)
- [httpx-retries 0.5.0 on PyPI (2026-04-20)](https://pypi.org/project/httpx-retries/)
- [httpx-retries source — `Retry._calculate_sleep` honors `Retry-After`](https://github.com/will-ockmore/httpx-retries/blob/main/httpx_retries/retry.py)
- [pybreaker 1.4.1 on GitHub (2025-09-21)](https://github.com/danielfm/pybreaker)
- [purgatory 3.0.1 on GitHub (2024-11-02)](https://github.com/mardiros/purgatory)
- [Zeitbach — Honoring the Retry-After header with Tenacity (2024-08-15)](https://zeitbach.com/blog/2024/08/15/honoring-the-retry-after-header-with-tenacity)
- [alexwlchan — handling-http-429-with-tenacity (reference impl)](https://github.com/alexwlchan/handling-http-429-with-tenacity)
- [respx user guide](https://lundberg.github.io/respx/guide/)
- [respx examples](https://lundberg.github.io/respx/examples/)
- [Python asyncio.Task — strong-reference warning](https://docs.python.org/3/library/asyncio-task.html)
- [SuperFastPython — asyncio.Semaphore patterns](https://superfastpython.com/asyncio-semaphore/)
- [AWS Architecture Blog — Exponential backoff and jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) — canonical jitter formulation (flagged: 2015 origin, but doctrine reaffirmed in 2024 AWS Builders' Library)
- [DEV — Python Background Tasks (FastAPI + asyncio traps, 2026)](https://dev.to/kaushikcoderpy/python-background-tasks-asyncio-traps-fastapi-celery-2026-381i)
- [Instructor — Retry logic with tenacity (2025)](https://python.useinstructor.com/concepts/retrying/)
- [OneUpTime — Python retry with exponential backoff (2025-01-06)](https://oneuptime.com/blog/post/2025-01-06-python-retry-exponential-backoff/view)
