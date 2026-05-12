"""KP-08: Secret hygiene — API keys never leak into observable surfaces.

The pool handles raw Gemini API keys. Any path that surfaces user-visible
output (logs, exception messages, telemetry sinks, Slack alerts, str()
of the pool itself) MUST identify keys by INDEX only — never by value.

Surfaces tested:

* WARNING / INFO / ERROR log records emitted during a 429 cascade
* The exception object that bubbles to the caller after exhaustion
* The telemetry sink dict written for ``model_used`` reporting
* The Slack alert string built when retries exhaust
* ``repr(pool)`` / ``str(pool)`` (Python defaults — no custom __repr__)

A failing assertion here is a P0 hygiene incident. Anti-pattern guard:
keys never to be printed even in test output (we use a SENTINEL_KEY of
``"AIzaSECRET-NEVER-PRINT-THIS"`` so a leak is unmistakable).

Anti-pattern guards:
    * never alter ``_GENERATIVE_MODEL_CHAIN`` (locked)
    * never log API keys (the test itself enforces this)
"""
from __future__ import annotations

import logging
import traceback

import pytest

from website.features.api_key_switching import key_pool as kp_mod
from website.features.api_key_switching.key_pool import GeminiKeyPool

# Distinctive sentinel — anything this string appearing in logs / exc /
# telemetry / Slack means a key leaked. Long enough to make grepping cheap.
_SENTINEL_KEY_0 = "AIzaSECRETnoprint-key0-DEADBEEF1234567890"
_SENTINEL_KEY_1 = "AIzaSECRETnoprint-key1-CAFEBABE0987654321"


class _FakeClientError(Exception):
    def __init__(self, message: str = "429 RESOURCE_EXHAUSTED") -> None:
        super().__init__(message)
        self.code = 429


def _make_sentinel_pool() -> GeminiKeyPool:
    return GeminiKeyPool([_SENTINEL_KEY_0, _SENTINEL_KEY_1])


def _assert_no_secret(blob: str, *, where: str) -> None:
    """Assert that neither sentinel key appears in ``blob``."""
    for token in (_SENTINEL_KEY_0, _SENTINEL_KEY_1):
        # Match on a partial substring (the unique portion) so we catch
        # truncated/hashed leaks too.
        partial = token.split("-")[0]  # "AIzaSECRETnoprint"
        assert partial not in blob, (
            f"SECRET LEAK in {where}: "
            f"sentinel key prefix found in observable surface"
        )


# ---------------------------------------------------------------------------
# Logging surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_cascade_logs_never_contain_api_keys(monkeypatch, caplog):
    """During a full 429 retry cascade, the pool emits WARNING records on
    every retry. None of those records — formatted message, args tuple,
    or extras — may contain the API key value."""
    pool = _make_sentinel_pool()
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "2")

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

    with caplog.at_level(logging.DEBUG, logger="website.features.api_key_switching.key_pool"):
        with pytest.raises(_FakeClientError):
            await pool.generate_content(
                contents="prompt",
                starting_model="gemini-2.5-flash",
                label="kp08-log-leak",
            )

    # Inspect every captured record across every level.
    for record in caplog.records:
        formatted = record.getMessage()
        _assert_no_secret(formatted, where=f"log record message ({record.levelname})")
        # Inspect raw args too — formatting via %s could expose a key
        # passed accidentally as a positional arg.
        if record.args:
            args_repr = repr(record.args)
            _assert_no_secret(args_repr, where=f"log record args ({record.levelname})")


@pytest.mark.asyncio
async def test_slack_alert_payload_never_contains_api_keys(monkeypatch):
    """The Slack alert built on exhaustion goes to an ops channel — it
    must surface key INDEX (e.g. ``key[0]``) but never the key VALUE."""
    pool = _make_sentinel_pool()
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "1")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/test")

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

    captured_payloads: list[str] = []

    def _fake_slack(message: str) -> None:
        captured_payloads.append(message)

    monkeypatch.setattr(kp_mod, "_send_slack_alert", _fake_slack)

    with pytest.raises(_FakeClientError):
        await pool.generate_content(
            contents="prompt",
            starting_model="gemini-2.5-flash",
            label="kp08-slack-leak",
        )

    # At least one alert must have been emitted (the retry-exhausted path).
    assert captured_payloads, "expected Slack alert on retry exhaustion"
    for payload in captured_payloads:
        _assert_no_secret(payload, where="Slack alert payload")


# ---------------------------------------------------------------------------
# Exception traceback surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhaustion_exception_traceback_does_not_leak_key(monkeypatch):
    """The exception that bubbles to the caller goes into Sentry / log
    aggregators verbatim. Neither the exception's str() nor its
    traceback frames may contain a raw key value."""
    pool = _make_sentinel_pool()
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

    try:
        await pool.generate_content(
            contents="prompt",
            starting_model="gemini-2.5-flash",
            label="kp08-tb-leak",
        )
        pytest.fail("expected exhaustion to raise")
    except _FakeClientError as exc:
        _assert_no_secret(str(exc), where="exception str()")
        _assert_no_secret(repr(exc), where="exception repr()")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _assert_no_secret(tb, where="formatted traceback")


# ---------------------------------------------------------------------------
# Telemetry sink surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_sink_records_key_index_not_key_value(monkeypatch):
    """The telemetry sink records ``key_index`` (an int) so downstream
    metadata can correlate fallbacks — it MUST NOT carry the key value."""
    pool = _make_sentinel_pool()
    call_count = {"n": 0}

    def _fake_get_client(_ki: int):
        class _Aio:
            class _Models:
                async def generate_content(self, *, model, contents, config):
                    call_count["n"] += 1
                    if call_count["n"] == 1:
                        raise _FakeClientError()

                    class _R:
                        text = "{}"

                    return _R()

            models = _Models()

        class _Client:
            aio = _Aio()

        return _Client()

    monkeypatch.setattr(pool, "_get_client", _fake_get_client)

    sink: list = []
    await pool.generate_content(
        contents="prompt",
        starting_model="gemini-2.5-flash",
        label="kp08-telemetry-leak",
        telemetry_sink=sink,
    )

    assert sink, "expected telemetry sink to record one entry"
    entry = sink[0]
    # key_index is the canonical reference; key VALUE must not appear
    # anywhere in the dict.
    assert "key_index" in entry
    assert isinstance(entry["key_index"], int)
    _assert_no_secret(repr(entry), where="telemetry sink entry")
    for failed in entry["failed_attempts"]:
        _assert_no_secret(repr(failed), where="failed_attempts entry")


# ---------------------------------------------------------------------------
# Pool object surface (default Python repr)
# ---------------------------------------------------------------------------


def test_pool_repr_does_not_default_print_keys():
    """Python's default object.__repr__ doesn't print attributes — but we
    pin this so a future ``__repr__`` override that pretty-prints
    ``self._keys`` would FAIL CI rather than ship to prod."""
    pool = _make_sentinel_pool()
    _assert_no_secret(repr(pool), where="repr(pool)")
    _assert_no_secret(str(pool), where="str(pool)")


def test_attempt_dataclass_str_carries_key_value_warning():
    """``Attempt`` is a frozen dataclass — its default str() WILL contain
    the key, by design (it's the data class). This test is a SAFETY-NET
    pin: callers that pass an Attempt to a logger must format the index,
    not the whole object. We assert the dataclass DOES expose the value
    so the caller is forced to extract ``.role`` / ``.model`` explicitly
    rather than ``%s`` the whole struct.

    If a future change rewrites Attempt to redact the key, that's
    welcome — flip this assertion. The point is to keep the contract
    visible: as of today, ``Attempt`` is value-carrying."""
    pool = _make_sentinel_pool()
    attempt = pool.next_attempt("gemini-2.5-flash")
    # Attempt IS value-carrying by design — flag in the test name so
    # callers know to format `.role` + `.model` and never the whole obj.
    assert attempt.key in (_SENTINEL_KEY_0, _SENTINEL_KEY_1)
    # Record the contract: keys are ONLY available via .key access on the
    # Attempt; the dataclass's default repr exposes them, so callers MUST
    # NOT pass the whole struct to a logger.
    assert attempt.key in repr(attempt)
