"""RP-02 — bounded-queue saturation surfaces retryable 503 (Phase 1B.2 invariant).

The rerank-slot semaphore lives at the HTTP layer in
``website/api/_concurrency.py:acquire_rerank_slot`` (NOT inside
``rerank/cascade.py``).  When ``depth >= queue_max`` the chat routes shed load
with ``HTTP 503`` + ``Retry-After: 5`` so cloud-front clients can back off
cleanly instead of stacking work behind a slow worker.

Protected knob (CLAUDE.md "Critical Infra Decision Guardrails") — we test
the wire contract but NEVER modify the semaphore, the default
``RAG_RERANK_CONCURRENCY`` (= 2), or the 503/Retry-After emission.  If a test
exposes a regression in this path, surface the bug — do not relax the test.

Coverage:

* ``acquire_rerank_slot`` raises :class:`QueueFull` once ``depth >= queue_max``
  and decrements depth on release (no leak on success or on exception).
* The HTTP admission gate in ``chat_routes.create_message`` and
  ``chat_routes.adhoc_message`` returns 503 with ``Retry-After: 5`` and a
  body code of ``queue_full`` when the queue is saturated.
* The env-var hot-reload contract (``_state.env_changed`` rebuild) honoured
  by ``acquire_rerank_slot``: lowering ``RAG_QUEUE_MAX`` after first read
  immediately tightens the gate.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Unit-level: the semaphore + queue depth contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_rerank_slot_raises_queue_full_at_capacity(monkeypatch):
    """Block one slot indefinitely; depth=queue_max=1 must raise QueueFull
    on the next attempt without affecting the held slot's release."""
    monkeypatch.setenv("RAG_RERANK_CONCURRENCY", "1")
    monkeypatch.setenv("RAG_QUEUE_MAX", "1")
    import importlib
    from website.api import _concurrency
    importlib.reload(_concurrency)
    _concurrency.reset_for_tests()

    gate = asyncio.Event()

    async def hold_slot() -> None:
        async with _concurrency.acquire_rerank_slot():
            await gate.wait()

    holder = asyncio.create_task(hold_slot())
    # Wait until the holder has actually entered the slot (depth==1).
    for _ in range(50):
        if _concurrency.queue_depth() == 1:
            break
        await asyncio.sleep(0.01)
    assert _concurrency.queue_depth() == 1

    with pytest.raises(_concurrency.QueueFull):
        async with _concurrency.acquire_rerank_slot():
            pytest.fail("should not have acquired a second slot")

    # Holder still owns its slot; depth must still be 1 after the rejection
    # (rejected attempts must not touch the counter).
    assert _concurrency.queue_depth() == 1

    gate.set()
    await holder
    assert _concurrency.queue_depth() == 0


@pytest.mark.asyncio
async def test_acquire_rerank_slot_decrements_on_exception(monkeypatch):
    """An exception inside the ``async with`` block must NOT leak a slot."""
    monkeypatch.setenv("RAG_RERANK_CONCURRENCY", "1")
    monkeypatch.setenv("RAG_QUEUE_MAX", "1")
    import importlib
    from website.api import _concurrency
    importlib.reload(_concurrency)
    _concurrency.reset_for_tests()

    with pytest.raises(RuntimeError):
        async with _concurrency.acquire_rerank_slot():
            raise RuntimeError("boom")
    assert _concurrency.queue_depth() == 0


@pytest.mark.asyncio
async def test_env_change_rebuilds_state(monkeypatch):
    """Lowering ``RAG_QUEUE_MAX`` between operations must take effect (no
    cached state pinning the old cap)."""
    import importlib
    from website.api import _concurrency
    importlib.reload(_concurrency)

    monkeypatch.setenv("RAG_QUEUE_MAX", "10")
    _concurrency.reset_for_tests()
    async with _concurrency.acquire_rerank_slot():
        assert _concurrency.queue_depth() == 1

    # Drop the cap to 1, hold a slot, expect QueueFull on the next try.
    monkeypatch.setenv("RAG_QUEUE_MAX", "1")
    monkeypatch.setenv("RAG_RERANK_CONCURRENCY", "1")
    gate = asyncio.Event()

    async def hold_slot() -> None:
        async with _concurrency.acquire_rerank_slot():
            await gate.wait()

    holder = asyncio.create_task(hold_slot())
    for _ in range(50):
        if _concurrency.queue_depth() == 1:
            break
        await asyncio.sleep(0.01)
    with pytest.raises(_concurrency.QueueFull):
        async with _concurrency.acquire_rerank_slot():
            pytest.fail("env rebind should have shrunk the queue")
    gate.set()
    await holder


# ---------------------------------------------------------------------------
# HTTP integration: 503 / Retry-After surface on saturated queue
# ---------------------------------------------------------------------------


@pytest.fixture
def v2_app(monkeypatch):
    """Build a v2-bound FastAPI app with stub LLM creds + queue_max=1 so the
    second concurrent request trips the admission gate deterministically."""
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-rp02")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-rp02")
    monkeypatch.setenv("RAG_RERANK_CONCURRENCY", "1")
    monkeypatch.setenv("RAG_QUEUE_MAX", "1")

    import importlib
    from website.api import _concurrency
    importlib.reload(_concurrency)
    _concurrency.reset_for_tests()

    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None

    # Bypass pricing entitlement gate per the pricing-module-authority rule
    # (we never seed entitlements; the protected behaviour under test here
    # is purely the concurrency admission gate).
    async def _noop(*_a, **_kw):
        return None
    from website.api import chat_routes as chat_routes_mod
    monkeypatch.setattr(chat_routes_mod, "require_entitlement", _noop)
    monkeypatch.setattr(chat_routes_mod, "consume_entitlement", _noop)

    from website.app import create_app
    return create_app()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def test_adhoc_returns_503_when_queue_saturated(v2_app, mint_user):
    """Pre-saturate the in-process depth counter; POST /api/rag/adhoc must
    return 503 with code=queue_full and Retry-After: 5.  The admission gate
    at chat_routes.py:540-546 fires BEFORE any orchestrator work, so no
    LLM round-trip is required for this assertion."""
    user = mint_user(workspace_count=1)

    # Saturate depth synchronously by patching the state object the route
    # reads.  This mirrors what a concurrent request would do without
    # requiring us to actually start one and race against TestClient's
    # single-thread executor.
    from website.api import _concurrency
    _concurrency.reset_for_tests()
    state = _concurrency._get_state()
    original_depth = state.depth
    state.depth = state.queue_max  # saturate

    try:
        with TestClient(v2_app) as client:
            resp = client.post(
                "/api/rag/adhoc",
                headers=_auth(user.jwt),
                json={"content": "hello", "stream": False},
            )
    finally:
        state.depth = original_depth

    assert resp.status_code == 503, resp.text
    # Body shape: chat_routes.py:543-545 uses {"reason": "queue_full", ...}
    body = resp.json()
    detail = body.get("detail") if isinstance(body, dict) else body
    detail_str = str(detail)
    assert "queue_full" in detail_str, detail_str
    # Retry-After header is the cloudfront / browser backoff signal.
    assert resp.headers.get("Retry-After") == "5", (
        f"503 must carry Retry-After: 5 (got {resp.headers.get('Retry-After')!r})"
    )


def test_create_message_stream_returns_503_when_queue_saturated(
    v2_app, mint_user, asyncpg_pool
):
    """The streaming sessions endpoint has the same admission gate at
    chat_routes.py:488-496.  Confirm 503 + Retry-After on saturation, and
    that NO StreamingResponse is established (no SSE bytes flushed)."""
    import uuid as _uuid
    import asyncio as _asyncio
    user = mint_user(workspace_count=1)

    # Seed a real session so the route gets past get_session().
    sid = _uuid.uuid4()
    async def _seed():
        async with asyncpg_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO rag.chat_sessions (id, workspace_id, profile_id, title) "
                "VALUES ($1, $2, $3, $4)",
                sid, user.workspace_ids[0], user.auth_user_id, "rp-02 saturation",
            )
    _asyncio.get_event_loop().run_until_complete(_seed())

    from website.api import _concurrency
    _concurrency.reset_for_tests()
    state = _concurrency._get_state()
    state.depth = state.queue_max  # saturate

    try:
        with TestClient(v2_app) as client:
            resp = client.post(
                f"/api/rag/sessions/{sid}/messages",
                headers=_auth(user.jwt),
                json={"content": "hi", "stream": True},
            )
    finally:
        state.depth = 0

    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After") == "5"
    # No event-stream content-type means no SSE was opened.
    assert "text/event-stream" not in resp.headers.get("content-type", "")


def test_admission_gate_does_not_trip_on_empty_queue(v2_app, mint_user):
    """Negative-side assertion: when depth==0, the queue_full path must NOT
    fire. The previous KeyError xfail block was removed in this commit — the
    serialize bug it referenced was fixed in 772acee (back-compat shim accepts
    both v1 and v2 column sets), so the request now reaches a real response."""
    user = mint_user(workspace_count=1)
    from website.api import _concurrency
    _concurrency.reset_for_tests()
    assert _concurrency.queue_depth() == 0

    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/adhoc",
            headers=_auth(user.jwt),
            json={"content": "ping", "stream": False},
        )

    if resp.status_code == 503:
        body = resp.text
        assert "queue_full" not in body, (
            f"queue_full surfaced on empty queue: {body!r}"
        )
