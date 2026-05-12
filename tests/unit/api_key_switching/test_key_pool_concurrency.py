"""KP-03: Concurrent pool contention.

When N async workers race to grab a key while one (key, model) slot is on
cooldown, each worker MUST receive a slot that is still serviceable — no
worker should be handed a slot that is currently cooled.

Two angles are exercised:

1. ``next_attempt`` — fast path used by callers that pre-select before
   issuing the SDK call. Verifies all concurrent callers get a non-cooled
   slot back.
2. ``generate_content`` — full path. Uses a fake client whose
   ``generate_content`` co-routine yields control via ``asyncio.sleep(0)``
   so the asyncio scheduler interleaves N coroutines through the pool's
   chain-walk + cooldown bookkeeping.

Anti-pattern guards:
    * never alter ``_GENERATIVE_MODEL_CHAIN`` (locked)
    * never log API keys
"""
from __future__ import annotations

import asyncio

import pytest

from website.features.api_key_switching.key_pool import GeminiKeyPool


def _make_pool(num_keys: int = 3) -> GeminiKeyPool:
    return GeminiKeyPool([f"AIza{i}" for i in range(num_keys)])


# ---------------------------------------------------------------------------
# next_attempt under cooldown contention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_attempt_skips_cooled_slot_under_concurrency():
    """All concurrent callers see the cooled (key0, flash) slot skipped and
    receive a key whose role serves traffic right now."""
    pool = _make_pool(num_keys=3)
    pool._mark_cooldown(key_index=0, model="gemini-2.5-flash", attempt=1)

    async def _grab() -> str:
        # Async wrapper around the sync next_attempt so we can gather() it.
        return pool.next_attempt("gemini-2.5-flash").key

    results = await asyncio.gather(*[_grab() for _ in range(20)])
    # Every concurrent caller must get a non-cooled key (AIza1 or AIza2).
    # AIza0 IS cooled at flash and would be wrong to hand out — even though
    # the chain falls through to (0, flash-lite) eventually, next_attempt
    # picks the first key on the chain HEAD which is now (1, flash).
    for picked in results:
        assert picked in {"AIza1", "AIza2"}, picked


@pytest.mark.asyncio
async def test_next_attempt_when_all_slots_cooled_falls_through_safely():
    """When EVERY (key, model) slot is on cooldown, ``next_attempt`` falls
    back to retrying the full chain rather than raising — fail-fast is
    opt-in via env, not the default."""
    pool = _make_pool(num_keys=2)
    for ki in (0, 1):
        for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            pool._mark_cooldown(ki, model, attempt=1)

    async def _grab():
        return pool.next_attempt("gemini-2.5-flash")

    # 10 concurrent callers — all should get a key (the fallback "retry
    # full chain" path), not a RuntimeError.
    results = await asyncio.gather(
        *[_grab() for _ in range(10)], return_exceptions=True
    )
    # No exceptions. Every result is an Attempt with a recognised key.
    for r in results:
        assert not isinstance(r, BaseException), r
        assert r.key in {"AIza0", "AIza1"}


@pytest.mark.asyncio
async def test_next_attempt_fail_fast_env_raises_when_all_cooled(monkeypatch):
    """With ``GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS=1``, exhaustion raises a
    deterministic RuntimeError instead of looping on stale slots — tested
    under concurrent contention."""
    pool = _make_pool(num_keys=1)
    pool._mark_cooldown(0, "gemini-2.5-flash", attempt=1)
    pool._mark_cooldown(0, "gemini-2.5-flash-lite", attempt=1)
    monkeypatch.setenv("GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS", "1")

    async def _grab():
        return pool.next_attempt("gemini-2.5-flash")

    results = await asyncio.gather(
        *[_grab() for _ in range(8)], return_exceptions=True
    )
    # Every concurrent caller must observe the same RuntimeError under
    # fail-fast mode — no caller silently gets a still-cooled slot.
    for r in results:
        assert isinstance(r, RuntimeError)
        assert "cooldown" in str(r).lower()


# ---------------------------------------------------------------------------
# generate_content concurrent fan-out + cooldown bookkeeping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_generate_content_each_gets_serviceable_key(monkeypatch):
    """N concurrent generate_content calls all complete via a non-cooled
    key. The pool advances ``_next_gen_key`` round-robin on success — under
    concurrency the bookkeeping must not point at a cooled slot."""
    pool = _make_pool(num_keys=3)
    pool._mark_cooldown(key_index=0, model="gemini-2.5-flash", attempt=1)

    served_by: list[int] = []

    def _fake_get_client(key_index: int):
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    # Yield control so the scheduler interleaves coroutines.
                    await asyncio.sleep(0)
                    served_by.append(key_index)

                    class _R:
                        text = "{}"

                    return _R()

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    async def _call(i: int):
        return await pool.generate_content(
            contents=f"prompt-{i}",
            starting_model="gemini-2.5-flash",
            label=f"kp03-{i}",
        )

    results = await asyncio.gather(*[_call(i) for i in range(15)])
    # Every call returned the success triple: (resp, model_used, key_used)
    assert len(results) == 15
    for _resp, model_used, key_used in results:
        assert model_used == "gemini-2.5-flash"
        # Cooled slot (0, flash) must NEVER have been used — this is the
        # KP-03 race-free-selection invariant.
        assert key_used != 0
    # Servers used: only non-cooled keys ever served traffic. The exact
    # round-robin distribution between key 1 and key 2 depends on async
    # scheduling and ``_next_gen_key`` advance ordering; what matters for
    # KP-03 is that cooled key 0 is NEVER selected.
    assert set(served_by).issubset({1, 2})
    assert 0 not in served_by


@pytest.mark.asyncio
async def test_purge_expired_is_idempotent_under_concurrency():
    """``_purge_expired`` is called from every chain build / next_attempt;
    concurrent calls (no asyncio yields between mutations of the dict)
    must not raise ``RuntimeError: dictionary changed size during
    iteration``. This protects against a regression that would only show
    up under load."""
    pool = _make_pool(num_keys=2)
    # Seed cooldowns that have already expired so the purge actually
    # mutates the dict on every call.
    import time

    past = time.monotonic() - 1.0
    for ki in (0, 1):
        for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            pool._cooldowns[(ki, model)] = past

    async def _purge_then_chain():
        for _ in range(50):
            pool._build_attempt_chain(starting_model="gemini-2.5-flash")
            await asyncio.sleep(0)
        return True

    # 8 concurrent purgers — none should raise.
    results = await asyncio.gather(
        *[_purge_then_chain() for _ in range(8)], return_exceptions=True
    )
    for r in results:
        assert r is True, r
    # After all the purging the cooldown dict must be empty.
    assert pool._cooldowns == {}
