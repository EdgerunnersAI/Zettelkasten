"""KP-07: All-keys-exhausted graceful degradation.

The pool's responsibility on exhaustion (every key/model combination has
been tried up to ``GEMINI_MAX_RETRIES`` and still 429'd or otherwise
failed) is to raise the LAST exception so the caller (summarization
engine, RAG synthesis, etc.) can convert that into a user-facing
``is_raw_fallback=True`` response instead of letting the request bubble
to a 5xx.

These tests verify the pool's exhaustion contract:

* ``generate_content`` raises after ``max_retries`` 429s with the upstream
  exception (not a generic RuntimeError) so the caller can introspect.
* ``generate_content`` raises ``RuntimeError`` when fail-fast is set and
  every slot is on cooldown at chain-build time (no client call made).
* ``embed_content_safe`` swallows the exception and returns ``None`` —
  this is the pool's own graceful-degradation path that the embedding
  callers depend on (KG features cache None and skip, never 500).

Anti-pattern guards:
    * never alter ``_GENERATIVE_MODEL_CHAIN`` (locked)
    * never log API keys
"""
from __future__ import annotations

import pytest

from website.features.api_key_switching.key_pool import GeminiKeyPool


def _make_pool(num_keys: int = 2) -> GeminiKeyPool:
    return GeminiKeyPool([f"AIza{i}" for i in range(num_keys)])


class _FakeClientError(Exception):
    """Mimic google-genai 429 RESOURCE_EXHAUSTED detection."""

    def __init__(self, message: str = "429 RESOURCE_EXHAUSTED quota exceeded") -> None:
        super().__init__(message)
        self.code = 429


@pytest.mark.asyncio
async def test_generate_content_raises_upstream_exc_after_max_retries(monkeypatch):
    """When every retry 429s, the pool raises the LAST upstream exception
    so the caller can build a meaningful raw-fallback (KP-07 contract)."""
    pool = _make_pool(num_keys=1)
    # Tighten max_retries so the test runs fast — even at 1 the contract
    # is identical: bubble the upstream exception.
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "1")

    def _fake_get_client(_ki: int):
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    raise _FakeClientError()

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    with pytest.raises(_FakeClientError) as excinfo:
        await pool.generate_content(
            contents="prompt",
            starting_model="gemini-2.5-flash",
            label="kp07-exhaust",
        )
    # Caller can introspect the exception to build raw-fallback metadata.
    assert "429" in str(excinfo.value)


@pytest.mark.asyncio
async def test_generate_content_raises_runtime_when_all_cooled_fail_fast(monkeypatch):
    """Under ``GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS=1``, exhaustion at chain
    build time raises ``RuntimeError`` BEFORE any client call — caller
    converts that into raw-fallback without hammering the upstream."""
    pool = _make_pool(num_keys=1)
    monkeypatch.setenv("GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS", "1")
    pool._mark_cooldown(0, "gemini-2.5-flash", attempt=1)
    pool._mark_cooldown(0, "gemini-2.5-flash-lite", attempt=1)

    client_calls = {"n": 0}

    def _fake_get_client(_ki: int):
        client_calls["n"] += 1
        raise AssertionError("client must NOT be created when chain is empty")

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    with pytest.raises(RuntimeError, match="cooldown"):
        await pool.generate_content(
            contents="prompt",
            starting_model="gemini-2.5-flash",
            label="kp07-fail-fast",
        )
    # No client was created — exhaustion detected at chain-build time.
    assert client_calls["n"] == 0


@pytest.mark.asyncio
async def test_generate_content_non_retryable_propagates_immediately(monkeypatch):
    """Non-retryable errors (e.g. 400 InvalidArgument) MUST NOT be looped
    over the chain — they bubble immediately so the caller can mark the
    request as a permanent failure rather than a quota issue."""
    pool = _make_pool(num_keys=2)
    call_count = {"n": 0}

    def _fake_get_client(_ki: int):
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    call_count["n"] += 1
                    raise ValueError("400 InvalidArgument: bad prompt")

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    with pytest.raises(ValueError, match="400 InvalidArgument"):
        await pool.generate_content(
            contents="prompt",
            starting_model="gemini-2.5-flash",
            label="kp07-non-retry",
        )
    # Pool aborted on the first non-retryable error rather than burning
    # through the chain.
    assert call_count["n"] == 1


def test_embed_content_safe_swallows_exhaustion(monkeypatch):
    """``embed_content_safe`` is the embedding-side graceful path — when
    the underlying ``embed_content`` exhausts, it returns ``None`` (KG
    features cache None and skip, never 500)."""
    pool = _make_pool(num_keys=1)

    def _fake_get_client(_ki: int):
        class _Models:
            def embed_content(self, *, model, contents, config):
                raise _FakeClientError()

        class _Client:
            class models:
                @staticmethod
                def embed_content(*, model, contents, config):
                    raise _FakeClientError()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    # The safe wrapper MUST return None on total exhaustion — never raise.
    out = pool.embed_content_safe("text to embed")
    assert out is None
