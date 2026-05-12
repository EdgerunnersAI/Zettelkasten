"""KP-02 + KP-04: Key-first traversal order and 429 cascade discipline.

Locked architectural decision (CLAUDE.md ‘Critical Infra Decision Guardrails’ +
``api_key_switching`` test plan KP-02): the attempt chain MUST iterate every
KEY at the same model BEFORE downgrading to the next model. Reversing this
turns rate-limit pressure on flash into an immediate, silent quality
regression to flash-lite (and burns paid quota during outages).

KP-02 — chain construction order
KP-04 — runtime 429 → next key (same model) before model downgrade

Anti-pattern guards:
    * never alter ``_GENERATIVE_MODEL_CHAIN`` (locked)
    * never log API keys
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from website.features.api_key_switching.key_pool import (
    _GENERATIVE_MODEL_CHAIN,
    GeminiKeyPool,
)


# ---------------------------------------------------------------------------
# Locked-chain sanity guard (a regression test for the model order itself)
# ---------------------------------------------------------------------------


def test_locked_generative_model_chain_unchanged():
    # Anti-pattern guard: this list is INFRA — flipping it changes
    # default-quality + cost characteristics across the entire app.
    # Touching the order requires explicit operator approval.
    assert _GENERATIVE_MODEL_CHAIN == [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]


# ---------------------------------------------------------------------------
# KP-02: chain construction is key-first (all keys per model before downgrade)
# ---------------------------------------------------------------------------


def _make_pool(num_keys: int = 3) -> GeminiKeyPool:
    return GeminiKeyPool([f"AIza{i}" for i in range(num_keys)])


def test_chain_iterates_all_keys_at_first_model_before_downgrade():
    pool = _make_pool(num_keys=3)
    chain = pool._build_attempt_chain(starting_model="gemini-2.5-flash")
    # Expected order: (0, flash), (1, flash), (2, flash), (0, flash-lite),
    # (1, flash-lite), (2, flash-lite). Anything else is a regression of
    # the locked decision.
    assert chain == [
        (0, "gemini-2.5-flash"),
        (1, "gemini-2.5-flash"),
        (2, "gemini-2.5-flash"),
        (0, "gemini-2.5-flash-lite"),
        (1, "gemini-2.5-flash-lite"),
        (2, "gemini-2.5-flash-lite"),
    ]


def test_chain_starting_model_lite_promotes_lite_to_first_position():
    pool = _make_pool(num_keys=2)
    chain = pool._build_attempt_chain(starting_model="gemini-2.5-flash-lite")
    # When the caller asks for flash-lite first, the chain still iterates
    # ALL keys at flash-lite before crossing into flash — same key-first
    # invariant just with a different starting model.
    assert chain == [
        (0, "gemini-2.5-flash-lite"),
        (1, "gemini-2.5-flash-lite"),
        (0, "gemini-2.5-flash"),
        (1, "gemini-2.5-flash"),
    ]


def test_chain_no_starting_model_uses_default_chain_order():
    pool = _make_pool(num_keys=2)
    chain = pool._build_attempt_chain(starting_model=None)
    assert chain == [
        (0, "gemini-2.5-flash"),
        (1, "gemini-2.5-flash"),
        (0, "gemini-2.5-flash-lite"),
        (1, "gemini-2.5-flash-lite"),
    ]


def test_chain_skips_cooled_down_slots():
    pool = _make_pool(num_keys=2)
    # Force key 0 / flash on cooldown — chain should skip it but keep the
    # rest of the key-first order intact.
    pool._mark_cooldown(key_index=0, model="gemini-2.5-flash", attempt=1)
    chain = pool._build_attempt_chain(starting_model="gemini-2.5-flash")
    assert (0, "gemini-2.5-flash") not in chain
    # First attempt is now key 1 / flash (still key-first; we only lost slot 0).
    assert chain[0] == (1, "gemini-2.5-flash")


def test_chain_when_all_slots_cooled_returns_full_chain_for_retry():
    pool = _make_pool(num_keys=1)
    # Cool every slot.
    for model in _GENERATIVE_MODEL_CHAIN:
        pool._mark_cooldown(key_index=0, model=model, attempt=1)
    # Without GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS set, the pool must return
    # the FULL chain (a hopeful retry) — not an empty list, which would
    # surface as a 5xx to the user.
    chain = pool._build_attempt_chain(starting_model="gemini-2.5-flash")
    assert len(chain) == len(_GENERATIVE_MODEL_CHAIN)


# ---------------------------------------------------------------------------
# KP-04: 429 on key0/flash → key1/flash (NOT flash-lite) at runtime
# ---------------------------------------------------------------------------


class _FakeClientError(Exception):
    """Mimic google-genai ClientError for ``_is_rate_limited`` detection."""

    def __init__(self, message: str = "429 RESOURCE_EXHAUSTED") -> None:
        super().__init__(message)
        # _is_rate_limited's first branch checks isinstance(ClientError) AND
        # .code == 429. We don't satisfy isinstance, but we DO satisfy the
        # second branch (string-sniff for "429" + "RESOURCE_EXHAUSTED").
        self.code = 429


def _success_resp(text: str = "{}"):
    class _R:
        pass

    r = _R()
    r.text = text
    return r


@pytest.mark.asyncio
async def test_429_on_first_key_advances_to_next_key_same_model(monkeypatch):
    """KP-04 core invariant: 429 on (key0, flash) → next attempt is
    (key1, flash), NOT (key0, flash-lite)."""
    pool = _make_pool(num_keys=2)

    # Per-(key_index, model) call-tracking: each call records (ki, model)
    # so we can assert on the actual traversal order the pool used.
    seen: list[tuple[int, str]] = []

    async def _fake_generate(model, contents, config):  # google-genai signature
        # Look up which key_index this client maps to via the closure.
        seen.append((_fake_generate._current_key, model))
        if _fake_generate._current_key == 0 and model == "gemini-2.5-flash":
            raise _FakeClientError()
        return _success_resp()

    _fake_generate._current_key = -1  # type: ignore[attr-defined]

    def _fake_get_client(key_index: int):
        # Return a pseudo-client whose .aio.models.generate_content closes
        # over key_index via a mutable attribute; we only ever have one
        # active call at a time in this test so the mutation is safe.
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    _fake_generate._current_key = key_index
                    return await _fake_generate(model, contents, config)

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    response, model_used, key_used = await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="kp04-traversal",
    )

    # Success was on (key1, flash) — NOT a downgrade to flash-lite.
    assert model_used == "gemini-2.5-flash"
    assert key_used == 1
    # Order of attempts: 429 on (0, flash), success on (1, flash). flash-lite
    # must NOT have been touched at all.
    assert seen == [
        (0, "gemini-2.5-flash"),
        (1, "gemini-2.5-flash"),
    ]


@pytest.mark.asyncio
async def test_all_keys_429_at_first_model_then_downgrades(monkeypatch):
    """When EVERY key is rate-limited at flash, the pool downgrades to
    flash-lite (key-first), still respecting the locked chain."""
    pool = _make_pool(num_keys=2)
    # Lift the default 3-retry cap so we get past 429s on both flash keys
    # AND start exercising the flash-lite downgrade in a single test run.
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "10")

    seen: list[tuple[int, str]] = []

    def _fake_get_client(key_index: int):
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    seen.append((key_index, model))
                    if model == "gemini-2.5-flash":
                        raise _FakeClientError()
                    return _success_resp()

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    response, model_used, key_used = await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="kp04-cascade",
    )

    # Success eventually landed on flash-lite (after BOTH flash keys 429'd).
    assert model_used == "gemini-2.5-flash-lite"
    # The first two attempts MUST be (0, flash), (1, flash) before any
    # flash-lite touch. This is the KP-04 invariant.
    assert seen[0] == (0, "gemini-2.5-flash")
    assert seen[1] == (1, "gemini-2.5-flash")
    assert seen[2][1] == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_telemetry_records_failed_attempts_for_fallback_reason(monkeypatch):
    """The telemetry sink must capture the first failed attempt so the
    surface that surfaces silent downgrades (summary metadata) has the
    fallback_reason — KP-04 observability dependency."""
    pool = _make_pool(num_keys=1)

    seen_calls = {"n": 0}

    def _fake_get_client(_ki: int):
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    seen_calls["n"] += 1
                    if seen_calls["n"] == 1:
                        raise _FakeClientError()
                    return _success_resp()

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)
    sink: list = []
    await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        telemetry_sink=sink,
        label="kp04-telemetry",
    )
    assert len(sink) == 1
    entry = sink[0]
    assert entry["model_used"] == "gemini-2.5-flash-lite"
    assert entry["starting_model"] == "gemini-2.5-flash"
    assert entry["fallback_reason"] == "gemini-2.5-flash-rate-limited"
    assert len(entry["failed_attempts"]) == 1
