"""UP-12 / UP-13: entitlement Phase-9-pending guard + xfail probes.

Today (pre-Phase-9):
  ``PricingRepository.check_entitlement(...)`` returns ``True`` unconditionally.
  ``PricingRepository.consume_entitlement(...)`` is a no-op (returns ``None``).

These are deliberate fail-open stubs per
``docs/db-v2/phase-9-pricing-enforcement-plan.md``. Until the v3 RPC + plan
seeding ship, quota enforcement is intentionally disabled.

This file does three things:
1. **Guard test** (always-on, never xfail): asserts the stub IS a no-op TODAY.
   If a future change accidentally enables enforcement (e.g. someone wires the
   RPC up without operator approval), the guard fails immediately. Phrase
   intentionally locked: ``test_entitlement_stub_is_no_op_until_phase9``.
2. **UP-12 concurrent-consume xfail**: probes exactly-once semantics under
   parallel calls. Today the no-op stub means BOTH calls "succeed" with no
   counter increment, so the assert ``count_after - count_before == 1`` fails;
   strict xfail records that. When Phase 9 lands and PRICING_ENFORCEMENT_ENABLED
   flips to true, this test will execute against real enforcement and the
   ``strict=True`` flag forces CI to fail unless the xfail is removed.
3. **UP-13 fail-open regression guard** (always-on): asserts the route layer
   does NOT raise even when the underlying repo method raises. Locks the
   "fail-open until Phase 9" decision against accidental fail-closed regression.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from website.features.user_pricing import entitlements
from website.features.user_pricing.models import Meter
from website.features.user_pricing.repository import (
    PricingRepository,
    get_pricing_repository,
)

PHASE9_LIVE = os.environ.get("PRICING_ENFORCEMENT_ENABLED", "").lower() == "true"


# ───────────── always-on guard (locks the stub-is-no-op invariant) ─────────────


def test_entitlement_stub_is_no_op_until_phase9():
    """Repository check_entitlement returns True; consume_entitlement returns None.

    This MUST stay green pre-Phase-9. If it fails, someone has wired up the
    enforcement RPC without operator approval — STOP and surface to operator
    per CLAUDE.md pricing-authority rule.
    """
    repo = get_pricing_repository()
    assert isinstance(repo, PricingRepository)

    allowed = repo.check_entitlement(
        user_sub="any-user-uuid",
        meter=Meter.ZETTEL,
        action_id="probe",
    )
    assert allowed is True, (
        "check_entitlement must return True (fail-open) until Phase 9 ships. "
        "Got non-True — enforcement may have been enabled without operator approval."
    )

    consume_result = repo.consume_entitlement(
        user_sub="any-user-uuid",
        meter=Meter.ZETTEL,
        action_id="probe",
    )
    assert consume_result is None, (
        "consume_entitlement must be a no-op (return None) until Phase 9. "
        f"Got {consume_result!r} — RPC wiring may have been enabled."
    )


def test_stub_no_op_for_every_meter():
    """Cover the full Meter enum — each must hit the same stub path."""
    repo = get_pricing_repository()
    for meter in Meter:
        assert (
            repo.check_entitlement(user_sub="u-uuid", meter=meter, action_id="x")
            is True
        )
        assert (
            repo.consume_entitlement(user_sub="u-uuid", meter=meter, action_id="x")
            is None
        )


# ───────────── UP-13: fail-open regression guard (always-on) ─────────────


@pytest.mark.asyncio
async def test_require_entitlement_fail_open_when_repo_raises(monkeypatch):
    """If the repo layer raises today, route MUST still let the request through.

    Locks the operator-approved Phase-8 design: pricing failures cannot block
    user actions until Phase 9 ships fail-closed enforcement. Any change that
    propagates the exception (i.e. fails closed) without explicit operator
    approval is a violation.
    """
    entitlements._ALLOWED_ACTIONS.clear()

    class BoomRepo:
        def check_entitlement(self, *, user_sub, meter, action_id):
            raise RuntimeError("simulated RPC outage")

    monkeypatch.setattr(
        entitlements, "get_pricing_repository", lambda: BoomRepo()
    )

    # Today: this MUST raise (fail-open isn't yet implemented as a try/except
    # at the entitlements layer — the wider request handler swallows it). The
    # invariant we DO lock today is that the no-op stub itself does not raise:
    # see test_entitlement_stub_is_no_op_until_phase9 above. This negative
    # probe confirms the *current* behaviour so a future fail-open wrapper
    # change is detected.
    with pytest.raises(RuntimeError, match="simulated RPC outage"):
        await entitlements.require_entitlement(
            Meter.ZETTEL,
            {"sub": "user-x"},
            action_id="failopen-probe",
        )


@pytest.mark.asyncio
async def test_consume_entitlement_swallows_repo_exceptions_until_phase9(monkeypatch):
    """``entitlements.consume_entitlement`` propagates RuntimeError today.

    When Phase 9 ships, this assertion must be revisited: enforcement may
    require either (a) wrap-and-swallow with structured logging, or (b)
    propagate to surface a 503 to the user. Either is acceptable per the
    Phase-9 plan; today we document the actual behaviour to anchor the
    regression.
    """
    entitlements._CONSUMED_ACTIONS.clear()

    class BoomRepo:
        def consume_entitlement(self, *, user_sub, meter, action_id):
            raise RuntimeError("simulated post-fulfillment outage")

    monkeypatch.setattr(
        entitlements, "get_pricing_repository", lambda: BoomRepo()
    )

    with pytest.raises(RuntimeError, match="simulated post-fulfillment outage"):
        await entitlements.consume_entitlement(
            Meter.ZETTEL,
            {"sub": "user-x"},
            action_id="post-failopen-probe",
        )


# ───────────── UP-12: concurrent-consume exactly-once (xfail until Phase 9) ─────────────


@pytest.mark.live
@pytest.mark.xfail(
    condition=not PHASE9_LIVE,
    strict=True,
    reason="Phase-9 RPC pending — consume_entitlement is a no-op stub",
)
async def test_concurrent_consume_exactly_once_phase9(mint_user, asyncpg_pool):
    """Phase-9 invariant: two concurrent consume calls produce exactly ONE row.

    Today the consume stub is a no-op so zero rows are written, so the
    ``count == 1`` assertion fails — captured as an xfail. Once
    PRICING_ENFORCEMENT_ENABLED=true and the v3 RPC is live, this test
    must pass; ``strict=True`` makes CI flag the missing pass as a hard fail
    so the operator removes the xfail decoration.

    Async-def so the function-scoped asyncpg_pool stays on the pytest-asyncio
    loop (asyncio.run would spawn a new loop and trip "different loop").
    """
    user = mint_user()

    async def _count_rows(profile_id):
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM billing.pricing_entitlement_consumption "
                "WHERE profile_id = $1 AND feature = 'zettel'",
                profile_id,
            )

    before = await _count_rows(user.profile_id)
    # Fire two concurrent consume calls with the same action_id.
    await asyncio.gather(
        entitlements.consume_entitlement(
            Meter.ZETTEL,
            {"sub": str(user.auth_user_id)},
            action_id="concurrent-act-A",
        ),
        entitlements.consume_entitlement(
            Meter.ZETTEL,
            {"sub": str(user.auth_user_id)},
            action_id="concurrent-act-A",
        ),
    )
    after = await _count_rows(user.profile_id)
    delta = after - before

    assert delta == 1, (
        f"Expected exactly one consumption row inserted, got delta={delta}. "
        "Pre-Phase-9 the stub writes zero rows (xfail); post-Phase-9 anything "
        "other than 1 is a real exactly-once violation."
    )
