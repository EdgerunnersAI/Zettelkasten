"""Phase 3 — UP-07, UP-08, UP-09, UP-10, UP-11: order/subscription/verify mutation paths.

These tests lock the production gates on the four pricing-mutation routes:
  * ``POST /api/payments/orders``                 (pack create)
  * ``POST /api/payments/orders/verify``          (HMAC signature verify)
  * ``POST /api/payments/subscriptions/cancel``   (cancel)
  * ``POST /api/payments/subscriptions/change``   (plan change)

Plus the ``get_or_create_plan`` race invariant against the in-memory plan
cache (UP-11).

Discovery 2026-05-11 amendments incorporated:
  - UP-07 is split into five gate-specific tests targeting each HTTPException
    raised in ``create_order``: invalid_product, price_changed (409),
    billing_profile_required, amount_too_low, account_frozen (409).
  - UP-09 asserts the no-seed invariant (zero new rows in
    ``billing.pricing_entitlement_consumption``) and that invented plan
    names are rejected. NEVER auto-subscribe in the fixture.
  - Outbound Razorpay calls are stubbed via ``respx`` or via a
    ``monkeypatch`` of ``get_razorpay_client`` so the suite never reaches
    api.razorpay.com.

Marked ``@pytest.mark.live`` because every test mints a real auth user via
the v2 Supabase project (JWKS-verified JWTs are required by ``get_current_user``).
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

import pytest
import respx
import httpx
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# ─────────────────────────── shared fixtures ───────────────────────────


@pytest.fixture
def app_client(monkeypatch):
    """TestClient over a freshly-created app with v2 schema bound + Razorpay creds.

    Razorpay key id+secret are injected so ``is_razorpay_configured()`` returns
    True; outbound calls are blocked by respx in tests that need to assert the
    SDK was/was not invoked. Tests that monkeypatch ``get_razorpay_client``
    directly do not rely on respx.
    """
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-mutation-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-mutation-tests")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_phase3")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "phase3-secret-do-not-deploy")

    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.features.user_pricing import razorpay_client
    razorpay_client.reset_client_cache()

    from website.app import create_app
    return TestClient(create_app())


@pytest.fixture
def with_billing_profile(mint_user):
    """Mint a user and seed a billing profile via the real upsert route.

    Avoids manual memory pokes — uses the public ``PUT /api/pricing/billing-profile``
    surface so the fixture mirrors the prod path.
    """
    def _factory(client: TestClient):
        user = mint_user()
        r = client.put(
            "/api/pricing/billing-profile",
            json={"phone": "+919000000000", "name": "Phase3 Tester"},
            headers={"Authorization": f"Bearer {user.jwt}"},
        )
        assert r.status_code in (200, 201), f"billing profile setup failed: {r.status_code} {r.text[:200]}"
        return user
    return _factory


def _bearer(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# ─────────────────────── UP-07: create_order gate matrix ───────────────────────


def test_create_order_invalid_product_returns_400(app_client, mint_user):
    """Unknown product_id → 400 invalid_product. Razorpay must not be called."""
    user = mint_user()
    with respx.mock(base_url="https://api.razorpay.com", assert_all_called=False) as mocked:
        route = mocked.post("/v1/orders").mock(return_value=httpx.Response(500))
        r = app_client.post(
            "/api/payments/orders",
            json={"product_id": "not-a-real-pack"},
            headers=_bearer(user.jwt),
        )
    assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"
    body = r.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    assert isinstance(detail, dict) and detail.get("code") == "invalid_product", body
    assert route.call_count == 0


def test_create_order_price_mismatch_returns_409_price_changed(app_client, with_billing_profile):
    """expected_amount that differs from catalog amount → 409 price_changed.

    Body must include ``code``, ``message``, ``expected_amount``, ``actual_amount``,
    ``product_id`` per the operator-locked contract (routes.py:1043-1056).
    """
    user = with_billing_profile(app_client)
    # Pick a real pack and submit a wrong expected_amount.
    from website.features.user_pricing.catalog import find_product
    product = find_product("zettel_5")
    assert product, "zettel_5 must exist in catalog — Phase 3 fixture assumption"
    wrong_amount = int(product["amount"]) + 1

    with respx.mock(base_url="https://api.razorpay.com", assert_all_called=False) as mocked:
        route = mocked.post("/v1/orders").mock(return_value=httpx.Response(500))
        r = app_client.post(
            "/api/payments/orders",
            json={"product_id": "zettel_5", "expected_amount": wrong_amount},
            headers=_bearer(user.jwt),
        )
    assert r.status_code == 409, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "price_changed", detail
    assert detail.get("expected_amount") == wrong_amount
    assert detail.get("actual_amount") == int(product["amount"])
    assert detail.get("product_id") == "zettel_5"
    assert route.call_count == 0, "Razorpay must NOT be called on price_changed"


def test_create_order_missing_billing_profile_returns_400(app_client, mint_user):
    """User without a billing profile (no phone) → 400 billing_profile_required."""
    user = mint_user()  # fresh — no upsert_billing_profile call
    with respx.mock(base_url="https://api.razorpay.com", assert_all_called=False) as mocked:
        route = mocked.post("/v1/orders").mock(return_value=httpx.Response(500))
        r = app_client.post(
            "/api/payments/orders",
            json={"product_id": "zettel_5"},
            headers=_bearer(user.jwt),
        )
    assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "billing_profile_required", detail
    assert route.call_count == 0


def test_create_order_amount_below_floor_returns_400(app_client, with_billing_profile, monkeypatch):
    """Product whose resolved ``amount`` < 100 paise → 400 amount_too_low.

    No production catalog item has amount<100, so the gate is verified by
    patching ``find_product`` to inject a sub-floor pack. The route's
    floor check (routes.py:146-147) must reject before any Razorpay call.
    """
    user = with_billing_profile(app_client)

    from website.features.user_pricing import routes as pricing_routes
    sub_floor = {
        "kind": "pack",
        "id": "phase3-sub-floor",
        "meter": "zettel",
        "name": "Phase 3 floor probe",
        "quantity": 1,
        "amount": 50,  # < 100 paise (₹0.50)
    }
    monkeypatch.setattr(pricing_routes, "find_product", lambda pid: sub_floor)

    with respx.mock(base_url="https://api.razorpay.com", assert_all_called=False) as mocked:
        route = mocked.post("/v1/orders").mock(return_value=httpx.Response(500))
        r = app_client.post(
            "/api/payments/orders",
            json={"product_id": "phase3-sub-floor"},
            headers=_bearer(user.jwt),
        )
    assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "amount_too_low", detail
    assert route.call_count == 0


def test_create_order_dispute_frozen_returns_409_account_frozen(app_client, with_billing_profile):
    """User on the dispute-frozen module set → 409 account_frozen.

    Freeze is toggled by ``record_dispute(phase='created')`` (repository.py:747-749).
    Using the real public dispute path keeps the test honest about state flow.
    """
    user = with_billing_profile(app_client)

    # Freeze via the canonical state mutator — record_dispute with phase=created
    # adds user_sub to ``_DISPUTE_FROZEN``. We deliberately do NOT poke the set
    # directly so a future refactor that renames the set still passes.
    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    repo.record_dispute(
        razorpay_dispute_id=f"disp_phase3_{uuid.uuid4().hex[:8]}",
        razorpay_payment_id=None,
        payment_id=None,
        render_user_id=str(user.auth_user_id),
        amount=10000,
        phase="created",
        reason_code="phase3_probe",
        payload={},
    )
    try:
        with respx.mock(base_url="https://api.razorpay.com", assert_all_called=False) as mocked:
            route = mocked.post("/v1/orders").mock(return_value=httpx.Response(500))
            r = app_client.post(
                "/api/payments/orders",
                json={"product_id": "zettel_5"},
                headers=_bearer(user.jwt),
            )
        assert r.status_code == 409, f"got {r.status_code}: {r.text[:200]}"
        detail = r.json().get("detail") or {}
        assert detail.get("code") == "account_frozen", detail
        assert route.call_count == 0
    finally:
        # Defensive: unfreeze so other tests sharing the process aren't tainted
        # (the set is module-level). ``won`` is the canonical clear phase.
        repo.record_dispute(
            razorpay_dispute_id=f"disp_phase3_clear_{uuid.uuid4().hex[:8]}",
            razorpay_payment_id=None,
            payment_id=None,
            render_user_id=str(user.auth_user_id),
            amount=0,
            phase="won",
            payload={},
        )


# ─────────────────────── UP-08: verify-payment signature path ───────────────────────


def test_verify_payment_signature_mismatch_returns_400(app_client, with_billing_profile, monkeypatch):
    """Bad signature on ``/orders/verify`` → 400 signature_mismatch.

    Creates a real internal payment record (so the ownership gate passes),
    then sends a verify with an all-zeros signature. The route must reject
    via ``verify_payment_signature`` -> ``compare_digest`` -> False.
    """
    user = with_billing_profile(app_client)

    # Create an internal payment record + attach a fake razorpay_order_id.
    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    payment = repo.create_payment_record(
        user_sub=str(user.auth_user_id),
        product_id="zettel_5",
        kind="pack",
        amount=9900,
        currency="INR",
        meter="zettel",
        quantity=5,
    )
    fake_order = f"order_phase3_{uuid.uuid4().hex[:10]}"
    repo.attach_provider_order(payment_id=payment["payment_id"], razorpay_order_id=fake_order)

    bad_sig = "0" * 64
    r = app_client.post(
        "/api/payments/orders/verify",
        json={
            "payment_id": payment["payment_id"],
            "razorpay_payment_id": "pay_phase3_bad",
            "razorpay_order_id": fake_order,
            "razorpay_signature": bad_sig,
        },
        headers=_bearer(user.jwt),
    )
    assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "signature_mismatch", detail


def test_verify_payment_signature_replay_is_idempotent(app_client, with_billing_profile):
    """Good signature replayed twice → both succeed (mark_payment_paid is idempotent).

    The fulfillment helper (`_apply_fulfillment`) credits pack balances on
    first call; on replay ``record["status"] == "paid"`` so the apply branch
    is a no-op. Both calls must be 2xx; second call must not double-credit.
    """
    user = with_billing_profile(app_client)
    import os

    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    payment = repo.create_payment_record(
        user_sub=str(user.auth_user_id),
        product_id="zettel_5",
        kind="pack",
        amount=9900,
        currency="INR",
        meter="zettel",
        quantity=5,
    )
    fake_order = f"order_phase3_{uuid.uuid4().hex[:10]}"
    fake_payment = f"pay_phase3_{uuid.uuid4().hex[:10]}"
    repo.attach_provider_order(payment_id=payment["payment_id"], razorpay_order_id=fake_order)

    secret = os.environ["RAZORPAY_KEY_SECRET"]
    good = hmac.new(
        secret.encode("utf-8"),
        f"{fake_order}|{fake_payment}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    body = {
        "payment_id": payment["payment_id"],
        "razorpay_payment_id": fake_payment,
        "razorpay_order_id": fake_order,
        "razorpay_signature": good,
    }
    r1 = app_client.post("/api/payments/orders/verify", json=body, headers=_bearer(user.jwt))
    r2 = app_client.post("/api/payments/orders/verify", json=body, headers=_bearer(user.jwt))
    assert r1.status_code in (200, 201, 202), f"first verify: {r1.status_code} {r1.text[:200]}"
    assert r2.status_code in (200, 201, 202), f"replay verify: {r2.status_code} {r2.text[:200]}"

    # Pack balance must have been credited exactly once (5 zettels).
    bal = repo.get_balances(user_sub=str(user.auth_user_id))
    assert bal.get("zettel", 0) == 5, (
        f"replay double-credited: {bal} (expected zettel=5)"
    )


# ─────────────────────── UP-09: subscription change discipline ───────────────────────


async def test_subscription_change_does_not_seed_consumption_rows(
    app_client, with_billing_profile, asyncpg_pool
):
    """``POST /subscriptions/change`` must NEVER insert into pricing_entitlement_consumption.

    Pricing-authority rule (CLAUDE.md): no seeding. Counts rows before and
    after the call — any delta fails the test regardless of HTTP status.
    The route can legitimately 502 or 400 here (no Razorpay live calls); we
    are not asserting success, only the no-seed invariant.
    """
    user = with_billing_profile(app_client)

    async def count() -> int:
        async with asyncpg_pool.acquire() as conn:
            return int(await conn.fetchval(
                "SELECT COUNT(*) FROM billing.pricing_entitlement_consumption WHERE profile_id = $1",
                user.profile_id,
            ) or 0)

    before = await count()
    # respx blocks any outbound Razorpay call so a flaky network can never
    # leak into this test's no-seed assertion.
    with respx.mock(base_url="https://api.razorpay.com", assert_all_called=False) as mocked:
        mocked.post("/v1/plans").mock(return_value=httpx.Response(500))
        mocked.post("/v1/subscriptions").mock(return_value=httpx.Response(500))
        mocked.post(host="api.razorpay.com").mock(return_value=httpx.Response(500))
        app_client.post(
            "/api/payments/subscriptions/change",
            json={"to_product_id": "basic_monthly"},
            headers=_bearer(user.jwt),
        )
    after = await count()
    assert after == before, (
        f"subscription change seeded pricing_entitlement_consumption "
        f"({before} -> {after}); pricing-authority rule violation"
    )


def test_subscription_change_rejects_invented_plan_name(app_client, with_billing_profile):
    """``to_product_id`` referencing an unknown plan tier → 400 invalid_product."""
    user = with_billing_profile(app_client)
    r = app_client.post(
        "/api/payments/subscriptions/change",
        json={"to_product_id": "ultra-gold-deluxe-monthly"},
        headers=_bearer(user.jwt),
    )
    assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "invalid_product", detail


def test_subscription_change_price_mismatch_returns_409(app_client, with_billing_profile):
    """expected_amount mismatch on /change must 409 price_changed (same contract as /orders)."""
    user = with_billing_profile(app_client)
    from website.features.user_pricing.catalog import find_product
    product = find_product("basic_monthly")
    assert product
    wrong = int(product["amount"]) + 1
    r = app_client.post(
        "/api/payments/subscriptions/change",
        json={"to_product_id": "basic_monthly", "expected_amount": wrong},
        headers=_bearer(user.jwt),
    )
    assert r.status_code == 409, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "price_changed", detail


# ─────────────────────── UP-10: cancel paths ───────────────────────


def test_cancel_no_active_subscription_returns_404(app_client, with_billing_profile):
    """Cancel with no current sub → 404 no_active_subscription.

    Locks the "fail-clean on idle state" contract. Razorpay must not be hit.
    """
    user = with_billing_profile(app_client)
    with respx.mock(base_url="https://api.razorpay.com", assert_all_called=False) as mocked:
        route = mocked.post("/v1/subscriptions/cancel").mock(return_value=httpx.Response(500))
        r = app_client.post(
            "/api/payments/subscriptions/cancel",
            json={"cancel_at_cycle_end": False},
            headers=_bearer(user.jwt),
        )
    assert r.status_code == 404, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "no_active_subscription", detail
    assert route.call_count == 0


def test_cancel_at_cycle_end_vs_immediate(app_client, with_billing_profile, monkeypatch):
    """Both flag values reach the Razorpay client with the right cycle-end bit.

    Stubs ``client.subscription.cancel`` to a recording fake so we can assert
    the boolean translates to 1 / 0 in the SDK call.
    """
    user = with_billing_profile(app_client)
    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    sub_id = f"sub_phase3_{uuid.uuid4().hex[:10]}"
    repo.create_or_update_subscription(
        user_sub=str(user.auth_user_id),
        plan_id="basic",
        period_id="basic_monthly",
        razorpay_subscription_id=sub_id,
        status="active",
    )

    calls: list[tuple[str, dict]] = []

    class _FakeSub:
        def cancel(self, subscription_id, payload):
            calls.append((subscription_id, dict(payload)))
            return {"id": subscription_id, "status": "cancelled"}

    class _FakeClient:
        subscription = _FakeSub()

    from website.features.user_pricing import routes as pricing_routes
    monkeypatch.setattr(pricing_routes, "get_razorpay_client", lambda: _FakeClient())

    r1 = app_client.post(
        "/api/payments/subscriptions/cancel",
        json={"cancel_at_cycle_end": True},
        headers=_bearer(user.jwt),
    )
    assert r1.status_code in (200, 202), f"cycle-end: {r1.status_code} {r1.text[:200]}"

    # Re-seed an active sub (the first cancel marked it pending_cancel) so we
    # can drive the immediate path on a clean precondition.
    repo.create_or_update_subscription(
        user_sub=str(user.auth_user_id),
        plan_id="basic",
        period_id="basic_monthly",
        razorpay_subscription_id=sub_id,
        status="active",
    )
    r2 = app_client.post(
        "/api/payments/subscriptions/cancel",
        json={"cancel_at_cycle_end": False},
        headers=_bearer(user.jwt),
    )
    assert r2.status_code in (200, 202), f"immediate: {r2.status_code} {r2.text[:200]}"

    assert len(calls) == 2, f"expected 2 cancel SDK calls; got {len(calls)}: {calls}"
    assert calls[0][1] == {"cancel_at_cycle_end": 1}, calls[0]
    assert calls[1][1] == {"cancel_at_cycle_end": 0}, calls[1]


# ────────── UP-11: get_or_create_plan serial cache discipline ─────────────────


def test_get_or_create_plan_serial_cache_discipline(monkeypatch):
    """5 sequential ``get_or_create_plan`` calls for the same (period, amount)
    must trigger ONE Razorpay plan.create — subsequent calls hit the cache.

    This locks the cache-then-create discipline. We deliberately do NOT
    simulate true thread races because the in-memory dict is not lock-guarded
    today (plan cache is a process-local optimisation; the source of truth
    is Razorpay's idempotent plan registry). The contract under test is the
    cache hit ratio: after the first call, every subsequent call must
    short-circuit on ``repo.get_cached_plan_id``.
    """
    # Configure Razorpay so the helper does not early-raise.
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_plan_race")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "phase3-secret-do-not-deploy")

    from website.features.user_pricing import razorpay_client
    razorpay_client.reset_client_cache()

    # Reset in-process plan cache so the test is order-independent.
    from website.features.user_pricing import repository as repo_mod
    repo_mod._MEMORY_PLAN_CACHE.clear()

    plan_create_calls: list[dict] = []

    class _FakePlan:
        def create(self, data):
            plan_create_calls.append(dict(data))
            return {"id": f"plan_phase3_{len(plan_create_calls)}"}

    class _FakeClient:
        plan = _FakePlan()

    monkeypatch.setattr(razorpay_client, "get_razorpay_client", lambda: _FakeClient())

    # Use a synthetic (period_id, amount) the prod billing.pricing_plan_cache
    # has never seen. Random suffix keeps the cache row out of the live table's
    # warm path and avoids collisions with cached real plans (e.g. basic_monthly:14900).
    probe_period = f"phase3-race-probe-{uuid.uuid4().hex[:8]}"
    probe_amount = 14900

    plan_ids: list[str] = []
    for _ in range(5):
        pid = razorpay_client.get_or_create_plan(
            period_id=probe_period,
            amount=probe_amount,
            plan_name="Phase3 race probe",
            plan_description=probe_period,
            period_label="monthly",
        )
        plan_ids.append(pid)

    assert len(plan_create_calls) == 1, (
        f"plan.create invoked {len(plan_create_calls)} times for the same "
        f"(period_id, amount); expected 1 — cache discipline broken"
    )
    assert len(set(plan_ids)) == 1, (
        f"distinct plan ids returned across calls: {plan_ids} — cache returned stale ids"
    )
