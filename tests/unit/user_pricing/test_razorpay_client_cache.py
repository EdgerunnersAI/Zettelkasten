"""UP-23: ``reset_client_cache()`` clears the memoised Razorpay client.

Regression-locks the test hook that powers per-test isolation. Without it,
the first test to call ``get_razorpay_client()`` would bind a client to its
RAZORPAY_KEY_ID/SECRET env and every subsequent test inheriting different
env values would silently reuse the stale client (and the auth header would
not match the new key). The hook is invoked in every Phase 1-4 fixture that
``monkeypatch.setenv`` the Razorpay creds.

Discovery 2026-05-11 confirmed ``reset_client_cache()`` already exists
(razorpay_client.py:91-99). This unit test is the explicit lock so an
accidental deletion of the helper or its ``cache_clear`` invocation flips
the suite red before reaching prod.
"""
from __future__ import annotations

import pytest

from website.features.user_pricing import razorpay_client


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Provide deterministic Razorpay creds so ``get_razorpay_client`` succeeds."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_cache_unit")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "cache-unit-secret")
    # Start from a clean cache regardless of preceding test order.
    razorpay_client.reset_client_cache()
    yield
    razorpay_client.reset_client_cache()


def test_reset_client_cache_returns_new_instance():
    """After reset, ``get_razorpay_client`` must construct a fresh client.

    razorpay.Client is constructed with ``auth=(key_id, secret)`` — without
    a cache reset, a re-read of mutated env vars would silently return the
    old auth and prod would HTTP-401 on every Razorpay call.
    """
    first = razorpay_client.get_razorpay_client()
    razorpay_client.reset_client_cache()
    second = razorpay_client.get_razorpay_client()
    assert first is not second, "reset_client_cache must clear the lru_cache"


def test_get_razorpay_client_is_memoised_without_reset():
    """Sanity check the lru_cache: two consecutive calls return the SAME instance."""
    first = razorpay_client.get_razorpay_client()
    second = razorpay_client.get_razorpay_client()
    assert first is second, "lru_cache(maxsize=1) must memoise across consecutive calls"


def test_reset_client_cache_tolerates_missing_cache_clear(monkeypatch):
    """If a test monkeypatches ``get_razorpay_client`` with a plain function
    (no ``cache_clear`` attribute), ``reset_client_cache()`` must NOT raise."""
    monkeypatch.setattr(
        razorpay_client,
        "get_razorpay_client",
        lambda: object(),  # plain callable, no cache_clear
    )
    # Must not raise even though the substituted callable lacks cache_clear.
    razorpay_client.reset_client_cache()
