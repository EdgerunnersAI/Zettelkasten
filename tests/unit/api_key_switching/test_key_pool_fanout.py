"""KP-FANOUT: rate-limit must fan out across ALL keys before giving up.

Regression guard for the bug observed 2026-05-13 where
``ops/scripts/_iter11_newsletter_heldout.py`` (with ``GEMINI_MAX_RETRIES=1``)
gave up after the FIRST 429 on key[0] instead of trying key[1] and the
billing key[2]. The old loop gated all retries on a single global counter;
the new loop tracks cooled keys per-request and walks the full chain.

Invariants:
* On 429, the failing key is cooled FOR THE REST OF THE REQUEST and skipped
  on subsequent chain slots (no wasted round-trip re-trying it on flash-lite).
* The chain is walked until either a key succeeds or every key is cooled.
* The "escalating_to=billing" alarm fires exactly once when crossing from
  the last free key to the billing key.
* When all keys 429, the upstream exception bubbles (KP-07 contract).
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from website.features.api_key_switching.key_pool import GeminiKeyPool


class _FakeClientError(Exception):
    """Mimic google-genai ClientError for ``_is_rate_limited`` detection."""

    def __init__(self, message: str = "429 RESOURCE_EXHAUSTED") -> None:
        super().__init__(message)
        self.code = 429


def _success_resp(text: str = "{}"):
    class _R:
        pass

    r = _R()
    r.text = text
    return r


def _pool_with_billing_last(num_keys: int = 3) -> GeminiKeyPool:
    """Build a pool where the LAST key is billing-tier — matches production
    where the third key is the paid fallback."""
    keys = [(f"AIza{i}", "free") for i in range(num_keys - 1)]
    keys.append((f"AIzaB{num_keys - 1}", "billing"))
    return GeminiKeyPool(keys)


def _install_fake_clients(pool: GeminiKeyPool, behavior, monkeypatch, seen):
    """Install a fake _get_client whose generate_content calls ``behavior``
    with (key_index, model) and either raises or returns its result."""

    def _fake_get_client(key_index: int):
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    seen.append((key_index, model))
                    return behavior(key_index, model)

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)


@pytest.mark.asyncio
async def test_429_fans_out_across_all_three_keys_until_success(monkeypatch, caplog):
    """key[0] 429, key[1] 429, key[2] (billing) succeeds.

    With the old global retry counter (and GEMINI_MAX_RETRIES=1) the pool
    gave up after key[0]. The fix walks the chain until a key works.
    """
    # Reproduce the exact operator env that surfaced the bug.
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "1")
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        if key_index in (0, 1):
            raise _FakeClientError()
        return _success_resp()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    with caplog.at_level(
        logging.WARNING, logger="website.features.api_key_switching.key_pool"
    ):
        response, model_used, key_used = await pool.generate_content(
            contents="prompt",
            starting_model="gemini-2.5-flash",
            label="fanout-success",
        )

    # Success eventually landed on the billing key — NOT a giving-up after key[0].
    assert key_used == 2
    assert model_used == "gemini-2.5-flash"
    # Chain walk: (0, flash) -> 429, (1, flash) -> 429, (2, flash) -> success.
    # Crucially, NO second touch of key[0] on flash-lite (cooled-skip works).
    assert seen[:3] == [
        (0, "gemini-2.5-flash"),
        (1, "gemini-2.5-flash"),
        (2, "gemini-2.5-flash"),
    ]
    # Key[0] and key[1] never reappear later in the chain walk.
    assert (0, "gemini-2.5-flash-lite") not in seen
    assert (1, "gemini-2.5-flash-lite") not in seen

    # Escalation alarm fires exactly once — at the boundary from last free
    # (key[1]) to billing (key[2]).
    escalation_records = [
        r for r in caplog.records if "escalating_to=billing" in r.getMessage()
    ]
    assert len(escalation_records) == 1, (
        f"expected exactly one escalating_to=billing log, "
        f"got {len(escalation_records)}: "
        f"{[r.getMessage() for r in escalation_records]}"
    )


@pytest.mark.asyncio
async def test_all_three_keys_429_raises_quota_exhausted(monkeypatch, caplog):
    """All three keys 429 on every model → upstream exception bubbles,
    a single all-keys-cooled error log is emitted (KP-07 contract)."""
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "1")
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        raise _FakeClientError()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    with caplog.at_level(
        logging.WARNING, logger="website.features.api_key_switching.key_pool"
    ):
        with pytest.raises(_FakeClientError) as excinfo:
            await pool.generate_content(
                contents="prompt",
                starting_model="gemini-2.5-flash",
                label="fanout-exhaust",
            )

    assert "429" in str(excinfo.value)

    # All three keys were tried (the original bug: only key[0] was tried).
    keys_tried = {ki for ki, _m in seen}
    assert keys_tried == {0, 1, 2}
    # Per-slot cool-down: no (key, model) slot was retried within the request.
    assert len(seen) == len(set(seen)), (
        f"a (key, model) slot was retried within one request: {seen}"
    )

    # Exactly one "all keys cooled" alarm at the end.
    final_alarm = [
        r for r in caplog.records if "all" in r.getMessage() and "cooled" in r.getMessage()
    ]
    assert len(final_alarm) == 1, (
        f"expected one all-keys-cooled alarm; got "
        f"{[r.getMessage() for r in final_alarm]}"
    )


@pytest.mark.asyncio
async def test_billing_key_actually_attempted_before_giving_up(monkeypatch):
    """The third (billing) key MUST be tried before we raise — this is the
    operator's last-resort paid fallback. Belt-and-braces guard on top of
    test_all_three_keys_429_raises_quota_exhausted."""
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "1")
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        raise _FakeClientError()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    with pytest.raises(_FakeClientError):
        await pool.generate_content(
            contents="prompt",
            starting_model="gemini-2.5-flash",
            label="fanout-billing-touched",
        )

    # Key 2 is the billing key (per _pool_with_billing_last). It MUST appear
    # in the call list.
    assert 2 in {ki for ki, _m in seen}, (
        "billing key was never attempted before raising — fanout broken"
    )


# ── W5 telemetry: counters + event ring ────────────────────────────────────


@pytest.mark.asyncio
async def test_success_increments_free_calls_for_free_key(monkeypatch):
    """A successful call on a free-tier key bumps _free_calls, not _billing_calls."""
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        return _success_resp()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="free-success",
    )

    # First key in the ordered chain is key[0] = free.
    assert pool._free_calls == 1
    assert pool._billing_calls == 0


@pytest.mark.asyncio
async def test_success_increments_billing_calls_for_billing_key(monkeypatch):
    """When the free keys 429 and the billing key serves the response,
    _billing_calls advances rather than _free_calls."""
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        if key_index in (0, 1):
            raise _FakeClientError()
        return _success_resp()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    _, _, key_used = await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="billing-success",
    )
    assert key_used == 2
    assert pool._billing_calls == 1
    assert pool._free_calls == 0


@pytest.mark.asyncio
async def test_rate_limit_appends_event_with_kind_rate_limit(monkeypatch):
    """Every 429 retry must append a kind='rate_limit' event to the ring."""
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        if key_index in (0, 1):
            raise _FakeClientError()
        return _success_resp()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="rate-limit-event",
    )

    rl_events = [ev for ev in pool._events if ev["kind"] == "rate_limit"]
    assert len(rl_events) == 2, f"expected 2 rate_limit events, got {list(pool._events)}"
    keys = {ev["key_index"] for ev in rl_events}
    assert keys == {0, 1}
    # S3: full shape includes the OTel-aligned key_role field.
    expected = {
        "ts", "kind", "key_index", "key_role",
        "model", "role", "attempt", "cooldown_secs",
    }
    for ev in rl_events:
        assert expected <= ev.keys()
        # key[0] and key[1] are free-tier in _pool_with_billing_last(3).
        assert ev["key_role"] == "free"


@pytest.mark.asyncio
async def test_rate_limit_emits_otel_counter(monkeypatch):
    """S3: when a 429 fires, _record_event should call
    budget.emit_rate_limited via the lazy import."""
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        if key_index in (0, 1):
            raise _FakeClientError()
        return _success_resp()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    calls: list[dict] = []

    def _spy(**kwargs):
        calls.append(kwargs)

    import website.features.summarization_engine.core.budget as budget_mod
    monkeypatch.setattr(budget_mod, "emit_rate_limited", _spy)

    await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="otel-rate-limit",
    )

    # Two 429s -> two emit calls. Each must carry the 4 required labels.
    assert len(calls) >= 2
    for c in calls:
        assert set(c.keys()) == {"summarizer", "role", "model", "key_role"}
        assert c["key_role"] == "free"
        assert c["model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_quota_exhausted_appends_event_on_escalation(monkeypatch):
    """When crossing free→billing the escalation alarm must also drop a
    kind='quota_exhausted' event into the ring."""
    pool = _pool_with_billing_last(num_keys=3)

    def _behavior(key_index, model):
        if key_index in (0, 1):
            raise _FakeClientError()
        return _success_resp()

    seen: list[tuple[int, str]] = []
    _install_fake_clients(pool, _behavior, monkeypatch, seen)

    await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="quota-exhausted-event",
    )

    qe_events = [ev for ev in pool._events if ev["kind"] == "quota_exhausted"]
    # Exactly one escalation event — from key[1] (last free) to key[2] (billing).
    assert len(qe_events) == 1, f"expected 1 quota_exhausted event, got {list(pool._events)}"
    assert qe_events[0]["role"] == "free"
    assert qe_events[0]["key_index"] == 1
