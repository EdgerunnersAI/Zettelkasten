"""WM-11 canary heartbeat unit tests.

Covers the 4 invariants from docs/research/2026-05-12-canary-heartbeat.md:
  1. Disabled-by-default: no HEARTBEAT_PING_URL → loop exits cleanly
  2. Probe path: one beat exercises key-pool sanity + outbound POST
  3. Self-failure: HTTP failure inside the loop is swallowed, loop continues
  4. Cancellation: stop event causes prompt loop exit (not _INTERVAL_S delay)
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from website.core import heartbeat as canary


# ── 1. disabled-by-default ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_immediately_when_url_unset(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_PING_URL", raising=False)
    stop = asyncio.Event()
    # No URL → loop must return without scheduling a wait
    await asyncio.wait_for(canary.heartbeat_loop(stop), timeout=1.0)


# ── 2. probe path ────────────────────────────────────────────────────────


class _StubKeyPool:
    def __init__(self, active: int) -> None:
        self._active = active

    def count_active_keys(self) -> int:
        return self._active


@pytest.mark.asyncio
async def test_one_beat_posts_to_ping_url_with_keys_active(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_PING_URL", "https://hc-ping.com/test-uuid")
    sent = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            sent["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content=None, **_kw):
            sent["url"] = url
            sent["content"] = content
            return httpx.Response(200)

    await canary._one_beat(
        key_pool_getter=lambda: _StubKeyPool(active=3),
        client_factory=_FakeClient,
    )
    assert sent["url"] == "https://hc-ping.com/test-uuid"
    assert sent["content"] == "keys_active=3"


@pytest.mark.asyncio
async def test_one_beat_no_op_when_url_unset(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_PING_URL", raising=False)
    factory_called = False

    class _ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            nonlocal factory_called
            factory_called = True

    await canary._one_beat(client_factory=_ShouldNotConstruct)
    assert factory_called is False


# ── 3. self-failure swallow ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_loop_swallows_beat_exceptions(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_PING_URL", "https://hc-ping.com/test-uuid")
    monkeypatch.setenv("HEARTBEAT_INTERVAL_S", "0.05")
    stop = asyncio.Event()
    beats = []

    async def _boom(*a, **kw):
        beats.append("boom")
        raise httpx.ConnectError("network down")

    with patch.object(canary, "_one_beat", side_effect=_boom):
        task = asyncio.create_task(canary.heartbeat_loop(stop))
        # Let at least 2 beats fire — proves the loop keeps going past one error.
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    assert len(beats) >= 2, f"expected >=2 beats despite errors, got {len(beats)}"


# ── 4. cancellation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_promptly_on_stop_event(monkeypatch):
    """stop.set() must short-circuit the inter-beat wait_for (not delay
    by _INTERVAL_S seconds — would block blue/green cutover)."""
    monkeypatch.setenv("HEARTBEAT_PING_URL", "https://hc-ping.com/test-uuid")
    monkeypatch.setenv("HEARTBEAT_INTERVAL_S", "60")  # would block ~60s without the wait_for
    stop = asyncio.Event()

    async def _quick_beat(**_kw):
        return None

    with patch.object(canary, "_one_beat", side_effect=_quick_beat):
        task = asyncio.create_task(canary.heartbeat_loop(stop))
        await asyncio.sleep(0.05)  # let the first beat fire + enter the wait
        stop.set()
        # Must exit within 1s, not 60s — proves wait_for(stop.wait()) pattern.
        await asyncio.wait_for(task, timeout=1.0)
