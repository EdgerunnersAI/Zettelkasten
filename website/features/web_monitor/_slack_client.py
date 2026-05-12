"""Shared Slack-webhook HTTP client for web_monitor.

WAVE-D Phase 1 (WM-05 + WM-07). One module so all 3 channels (App_Errors,
DO_Alerts, User_Activity) share identical retry / backoff / bounded-pool
semantics — adding a 4th channel now means using ``post_with_retry`` plus a
distinct ``SLACK_WEBHOOK_*`` env var; no per-channel reinvention.

Design (per docs/research/2026-05-12-slack-backoff.md, D-1 decision):

* Single ``stamina``-wrapped retry honoring Slack's ``Retry-After`` header
  on 429 via the ``on=_backoff_hook`` callable — returning a float from the
  hook overrides stamina's exp+jitter wait with the server-supplied value
  (capped at 60 s). Hard cap = _MAX_ATTEMPTS (4); no nested retry layers.
* Jittered exponential backoff for transient httpx errors (5xx / timeouts /
  conn-reset) via stamina's built-in ``wait_jitter`` (defeats thundering-
  herd on multi-worker setups).
* Bounded concurrent in-flight pool via ``asyncio.Semaphore(8)`` per worker.
  Production droplet runs 2 gunicorn workers, so the global cap is 16 — the
  hard ceiling chosen for the 2 GB / 1 vCPU box. Tasks are strong-ref'd in
  ``_inflight`` set so Python's GC cannot drop them mid-flight (CPython 3.12
  asyncio.Task warning).
* ``fire_and_forget`` schedules the bounded post coroutine and never raises;
  if the semaphore is saturated the caller still returns immediately (the
  ``async with _sem`` waits inside the spawned task, not the caller path).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import stamina

logger = logging.getLogger("website.web_monitor.slack_client")

# Per-worker bound. With 2 gunicorn workers on the prod droplet the global
# Slack-call ceiling is 16; under burst load extra calls queue on the
# semaphore rather than starting fresh httpx clients (each ~30 KB transient).
_MAX_INFLIGHT = 8
_sem = asyncio.Semaphore(_MAX_INFLIGHT)

# Strong-ref task set per CPython asyncio.Task warning — without this, tasks
# created via ``create_task`` and held only by a local may be GC'd mid-await.
_inflight: set[asyncio.Task] = set()

# Cap retry-after at 60s so a misbehaving Slack response can't pin a worker
# task for minutes during an outage. Slack's published policy is 1-30s.
_RETRY_AFTER_CAP_SECONDS = 60.0

# Total attempts for the stamina retry decorator. 4 attempts = 1 initial +
# 3 retries; with exp backoff + jitter, worst-case ~7-15s of waiting before
# the call gives up.
_MAX_ATTEMPTS = 4


class RateLimited(Exception):
    """Raised by ``_post_once`` on 429 to signal stamina to wait ``retry_after``.

    Custom exception (not httpx.HTTPStatusError) so the ``wait_fn`` below can
    safely extract the Retry-After value without sniffing response state out
    of stamina's RetryCallState.
    """

    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"rate_limited retry_after={retry_after}")
        self.retry_after = retry_after


def _backoff_hook(exc: Exception) -> bool | float:
    """Stamina ``on`` backoff hook — single decision point for retry + wait.

    Per stamina 26.x docs the ``on`` callable may return:
      * False  — do NOT retry (let the exception propagate)
      * True   — retry using stamina's exp+jitter schedule
      * float  — retry, overriding the wait with this exact value (seconds)

    We retry on transient httpx errors with exp+jitter, and on RateLimited
    we return the server-supplied Retry-After (capped) so Slack's polite-
    client contract is honored without a second outer loop. Net effect:
    hard cap of ``_MAX_ATTEMPTS`` total attempts — no compounding layers.
    """
    if isinstance(exc, RateLimited):
        if exc.retry_after is None:
            return True
        try:
            return min(float(exc.retry_after), _RETRY_AFTER_CAP_SECONDS)
        except (TypeError, ValueError):
            return True
    return isinstance(exc, httpx.HTTPError)


# Single stamina decorator drives all retries. Hard cap = _MAX_ATTEMPTS (4):
# 1 initial + 3 retries, no nested layers, total worst-case ~min(60s cap, 30s
# exp ceiling) × 3 = ~3 minutes only if every retry hits the max cap.
@stamina.retry(
    on=_backoff_hook,
    attempts=_MAX_ATTEMPTS,
    wait_initial=1.0,
    wait_jitter=2.0,
    wait_max=30.0,
)
async def _post_once(url: str, payload: dict[str, Any], timeout: float) -> httpx.Response:
    """Single Slack POST. Raises RateLimited / HTTPError to drive stamina retries."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload)
    if r.status_code == 429:
        # Slack's Retry-After header is always seconds in incoming-webhook
        # responses; tolerate string / int / None gracefully.
        ra = r.headers.get("Retry-After") or r.headers.get("retry-after")
        try:
            ra_f: float | None = float(ra) if ra is not None else None
        except (TypeError, ValueError):
            ra_f = None
        raise RateLimited(ra_f)
    if 500 <= r.status_code < 600:
        # raise_for_status triggers stamina retry via httpx.HTTPError branch.
        r.raise_for_status()
    return r


async def post_with_retry(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 10.0,
) -> httpx.Response | None:
    """POST to Slack with retry + Retry-After honoring. Returns response or None on final failure.

    Never raises — callers (App_Errors / DO_Alerts / User_Activity) all want
    "best effort post, log on failure" semantics. Retry / wait policy lives
    entirely in the ``_backoff_hook`` passed to stamina; this wrapper just
    converts the terminal exception into a logged ``None``.
    """
    try:
        return await _post_once(url, payload, timeout)
    except (RateLimited, httpx.HTTPError) as exc:
        logger.warning("slack_client: gave up after retries: %s", exc)
        return None
    except Exception:  # noqa: BLE001 — final guard; alerting must never raise
        logger.exception("slack_client: unexpected error during retry chain")
        return None


def fire_and_forget(coro_fn) -> asyncio.Task | None:
    """Schedule a Slack-emitting coroutine without blocking the caller.

    ``coro_fn`` is a zero-arg async callable that internally calls
    ``post_with_retry``. We wrap it in a semaphore-bounded shell so a burst
    of N callers cannot create N concurrent outbound httpx clients (each
    holds a TCP socket + TLS context); the semaphore caps in-flight Slack
    work at _MAX_INFLIGHT per worker.

    Returns the spawned Task so tests can ``await`` it. In production code
    paths the return value is ignored (true fire-and-forget).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Called from sync context with no running loop — fail soft. Caller
        # should never hit this in FastAPI request paths.
        logger.warning("slack_client: fire_and_forget called with no running loop")
        return None

    async def _bounded():
        async with _sem:
            try:
                await coro_fn()
            except Exception:  # noqa: BLE001 — alerting must never raise
                logger.exception("slack_client: fire_and_forget body raised")

    task = loop.create_task(_bounded())
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
    return task


def inflight_count() -> int:
    """Diagnostic accessor for test assertions + healthz.

    Counts tasks scheduled by ``fire_and_forget``, including any still
    waiting on the semaphore. For "active inside the semaphore" use
    ``semaphore_inflight_count``.
    """
    return len(_inflight)


def semaphore_inflight_count() -> int:
    """Count of coroutines currently past ``async with _sem`` (cap = _MAX_INFLIGHT).

    Computed from the semaphore's remaining capacity. Used by burst-stress
    tests to verify the semaphore actually caps concurrent Slack work — the
    raw ``inflight_count`` only tells you how many tasks exist, not how many
    have entered the critical section.
    """
    # ``_value`` is asyncio.Semaphore's internal remaining-permit counter; it
    # is a documented attribute on CPython and is the only zero-cost way to
    # read current acquisition without instrumenting the call sites.
    return _MAX_INFLIGHT - _sem._value


__all__ = [
    "post_with_retry",
    "fire_and_forget",
    "RateLimited",
    "inflight_count",
    "semaphore_inflight_count",
]
