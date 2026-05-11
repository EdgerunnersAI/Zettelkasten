"""UP-14: action-key cache isolation — same user/action dedupes; different user or meter must NOT bridge.

The cache backs the 15-min preflight guard in
``website.features.user_pricing.entitlements`` and lives in two module-level
dicts (``_ALLOWED_ACTIONS``, ``_CONSUMED_ACTIONS``). The key MUST be
(user_sub, str(meter), action_id) and MUST be None when ``action_id`` is
falsy — otherwise concurrent users at the same action_id would share a
cache hit (CWE-639 / IDOR-by-cache-bridge).
"""

from __future__ import annotations

import time

import pytest

from website.features.user_pricing import entitlements
from website.features.user_pricing.models import Meter


def test_same_user_same_action_dedupes_key():
    """Repeated calls with the same triple produce an equal key."""
    k1 = entitlements._action_key("user-a-uuid", Meter.ZETTEL, "act-1")
    k2 = entitlements._action_key("user-a-uuid", Meter.ZETTEL, "act-1")
    assert k1 == k2
    assert k1 is not None


def test_different_users_get_different_keys():
    """Cache MUST NOT bridge across users (CWE-639 guard)."""
    k1 = entitlements._action_key("user-a", Meter.ZETTEL, "act-1")
    k2 = entitlements._action_key("user-b", Meter.ZETTEL, "act-1")
    assert k1 != k2
    assert k1[0] != k2[0]


def test_different_meters_get_different_keys():
    """Quota for Zettel must not satisfy a Kasten check via the cache."""
    k_z = entitlements._action_key("user-a", Meter.ZETTEL, "act-1")
    k_k = entitlements._action_key("user-a", Meter.KASTEN, "act-1")
    k_r = entitlements._action_key("user-a", Meter.RAG_QUESTION, "act-1")
    assert len({k_z, k_k, k_r}) == 3


def test_missing_action_id_returns_none():
    """No action_id ⇒ no cache key (every call must hit the repo path)."""
    assert entitlements._action_key("user-a", Meter.ZETTEL, None) is None
    assert entitlements._action_key("user-a", Meter.ZETTEL, "") is None


def test_is_cached_hit_then_miss_after_ttl_expiry(monkeypatch):
    """``_is_cached`` returns True inside the 15-min window and False past it.

    Drive the clock via ``time.monotonic`` monkeypatch so the test is
    deterministic and does not actually sleep 901 seconds.
    """
    cache: dict[tuple[str, str, str], float] = {}
    key = ("u", str(Meter.ZETTEL), "act-x")

    fake_now = {"t": 1000.0}
    monkeypatch.setattr(entitlements.time, "monotonic", lambda: fake_now["t"])

    cache[key] = fake_now["t"]
    assert entitlements._is_cached(cache, key) is True

    # advance 1 second past TTL → expired
    fake_now["t"] += entitlements._ACTION_GUARD_TTL_SECONDS + 1
    assert entitlements._is_cached(cache, key) is False
    # stale entry was swept
    assert key not in cache


def test_is_cached_miss_for_unknown_key():
    cache: dict[tuple[str, str, str], float] = {}
    assert entitlements._is_cached(cache, ("u", "zettel", "nope")) is False


@pytest.mark.asyncio
async def test_require_entitlement_does_not_bridge_users(monkeypatch):
    """Two different users hitting the same action_id must each call the repo.

    Regression-locks the cache-isolation invariant at the require_entitlement
    boundary (not just the helper) so a future refactor that changes the cache
    key shape gets caught here too.
    """
    entitlements._ALLOWED_ACTIONS.clear()

    calls: list[str] = []

    class Repo:
        def check_entitlement(self, *, user_sub, meter, action_id):
            calls.append(user_sub)
            return True

    monkeypatch.setattr(entitlements, "get_pricing_repository", lambda: Repo())

    await entitlements.require_entitlement(Meter.ZETTEL, {"sub": "user-a"}, action_id="shared-action")
    await entitlements.require_entitlement(Meter.ZETTEL, {"sub": "user-b"}, action_id="shared-action")

    assert calls == ["user-a", "user-b"], (
        "Two distinct users sharing an action_id MUST both reach the repo — "
        "cache bridge would shrink calls to ['user-a'] only."
    )
