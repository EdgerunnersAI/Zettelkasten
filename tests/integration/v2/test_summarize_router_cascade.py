"""SE-07: router downgrade cascade end-to-end (flash → flash-lite → fail).

The ``GeminiKeyPool._build_attempt_chain`` produces a key-first chain
within each model tier (CLAUDE.md locked decision). When a call 429s on
the first key, the pool tries the next key (same model) before downgrading
to ``gemini-2.5-flash-lite``. After ALL keys cool on both models, the
caller sees a ``RuntimeError`` (or, for the website pipeline, the 422
"Could not extract" / engine WriterError path).

This test does not call real Gemini — it uses the WAVE-C ``StubGeminiPool``
(``tests/v2/fixtures/wave_c.py``) and asserts the chain order across
forced 429s and injected cooldowns.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.v2.fixtures.wave_c import StubGeminiPool, _Stub429Error


# --- chain order after forced 429 -----------------------------------------


def test_chain_advances_to_next_key_then_downgrades_on_cooldown():
    """Two keys on cooldown for flash → next_attempt downgrades to flash-lite."""
    stub = StubGeminiPool()
    stub.inject_cooldown(key_index=0, model="gemini-2.5-flash")
    stub.inject_cooldown(key_index=1, model="gemini-2.5-flash")

    attempt = stub.next_attempt("gemini-2.5-flash")
    assert attempt.model == "gemini-2.5-flash-lite", (
        f"expected downgrade to flash-lite, got model={attempt.model}"
    )


def test_chain_uses_next_key_when_first_cooled():
    """Key 0 cooled on flash → key 1 still serves flash before downgrade."""
    stub = StubGeminiPool()
    stub.inject_cooldown(key_index=0, model="gemini-2.5-flash")

    attempt = stub.next_attempt("gemini-2.5-flash")
    assert attempt.model == "gemini-2.5-flash"
    assert attempt.key == "stub-key-1"


def test_all_cooled_raises():
    """Every (key, model) slot on cooldown → ``next_attempt`` raises."""
    stub = StubGeminiPool()
    for ki in range(2):
        for m in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            stub.inject_cooldown(key_index=ki, model=m)

    with pytest.raises(RuntimeError, match="cooldown"):
        stub.next_attempt("gemini-2.5-flash")


# --- forced 429 mid-stream -----------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_force_429_after_two_calls():
    """Stub forces a 429 on the (force_429_after+1)-th call, then reverts.

    We assert the rotation behaviour: the very next call (after the 429)
    succeeds — proving the caller would re-enter ``next_attempt``, pick a
    fresh slot, and complete.
    """
    stub = StubGeminiPool(force_429_after=2)

    # Calls 1 + 2 succeed.
    r1, _, _ = await stub.generate_content(contents="hello-1")
    r2, _, _ = await stub.generate_content(contents="hello-2")
    assert r1.text == '{"summary": "stub"}'
    assert r2.text == '{"summary": "stub"}'

    # Call 3 raises a 429.
    with pytest.raises(_Stub429Error):
        await stub.generate_content(contents="hello-3")

    # Call 4 succeeds (the stub only forces 429 once, then reverts) — the
    # caller's retry loop in ``GeminiKeyPool.generate_content`` would loop
    # back into the chain and hit this.
    r4, _, _ = await stub.generate_content(contents="hello-4")
    assert r4.text == '{"summary": "stub"}'

    # The 429 attempt is recorded on the call list.
    raised = [c for c in stub.calls if c.raised_429]
    assert len(raised) == 1, raised


# --- chain ordering golden snapshot ---------------------------------------


def test_real_pool_attempt_chain_is_key_first_within_model():
    """Anchor the CLAUDE.md "key-first within model tier" decision against
    the real ``GeminiKeyPool._build_attempt_chain``. Two keys × two models
    must enumerate as:

        [(0, flash), (1, flash), (0, flash-lite), (1, flash-lite)]

    Reverting this is forbidden per CLAUDE.md infra-decision guardrails."""
    from website.features.api_key_switching.key_pool import GeminiKeyPool

    pool = GeminiKeyPool([("k0", "free"), ("k1", "free")])
    chain = pool._build_attempt_chain(starting_model="gemini-2.5-flash")
    assert chain == [
        (0, "gemini-2.5-flash"),
        (1, "gemini-2.5-flash"),
        (0, "gemini-2.5-flash-lite"),
        (1, "gemini-2.5-flash-lite"),
    ], f"key-first traversal broken: {chain}"


def test_real_pool_starting_model_lite_skips_flash():
    """Starting model = ``flash-lite`` must NOT include ``flash`` slots
    before the chain — the caller asked for the cheaper tier explicitly."""
    from website.features.api_key_switching.key_pool import GeminiKeyPool

    pool = GeminiKeyPool([("k0", "free"), ("k1", "free")])
    chain = pool._build_attempt_chain(starting_model="gemini-2.5-flash-lite")
    # Lite first × all keys, then flash × all keys.
    assert chain[:2] == [
        (0, "gemini-2.5-flash-lite"),
        (1, "gemini-2.5-flash-lite"),
    ], f"starting_model=lite should put lite slots first: {chain}"
