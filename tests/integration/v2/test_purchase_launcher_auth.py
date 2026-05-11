"""UP-24 Tier-A: logged-out POST to ``/api/payments/orders`` and the
sibling subscription / cancel endpoints must return a clean 401/403 — never
500, never a phantom 200, never an HTML auth redirect to a third party.

The launcher (``purchase_launcher.js``) is built around this contract:
``authToken()`` returns ``null`` when the user is signed-out, ``authHeaders()``
omits the ``Authorization`` header, and ``ensureBillingProfile()`` throws a
``not_authenticated`` error. The route-side counterpart is that
``get_current_user`` denies the call cleanly — that is what this test pins.

Tier-B (Chrome UX walk-through with DevTools assertions on
``localStorage`` and a click-while-logged-out probe) is intentionally
deferred to the Phase 7 final-PR end-to-end walkthrough — the launcher
secret-scan (UP-21) plus this Tier-A 401 contract are sufficient for the
Phase-4 gate.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def app_client(monkeypatch):
    """TestClient with v2 schema bound + Razorpay creds.

    Razorpay credentials are injected so we exercise the production-shaped
    error response — without them, ``is_razorpay_configured()`` could short-
    circuit to 503 before the auth dependency fires (which is the wrong
    contract under test).
    """
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-auth-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-auth-tests")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_phase4_launch_auth")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "phase4-launcher-auth-not-deployed")

    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.features.user_pricing import razorpay_client
    razorpay_client.reset_client_cache()

    from website.app import create_app
    return TestClient(create_app())


def _assert_auth_error_body(response_text: str) -> None:
    """The error body must hint at sign-in or auth so the launcher can show a
    friendly toast / inline modal instead of redirecting the user away.

    Accepts any of: 'sign', 'auth', 'login', 'token', 'credential' (case-
    insensitive) — gives the route freedom to evolve copy without breaking
    this contract. If FastAPI's default 'Not authenticated' body is what we
    get, 'auth' covers it.
    """
    lowered = response_text.lower()
    needles = ("sign", "auth", "login", "token", "credential")
    matched = [n for n in needles if n in lowered]
    assert matched, (
        f"401/403 body must hint at sign-in/auth (one of {needles}); got: "
        f"{response_text[:300]!r}"
    )


# ─────────────────────────── orders ───────────────────────────


def test_logged_out_create_order_returns_401(app_client):
    r = app_client.post(
        "/api/payments/orders",
        json={"product_id": "zettel-pack-100", "expected_amount": 9900, "source": "pricing"},
    )
    # FastAPI HTTPBearer with auto_error=False then a downstream guard could
    # surface as either 401 or 403. The launcher treats both as "sign-in".
    assert r.status_code in (401, 403), r.text
    _assert_auth_error_body(r.text)


def test_logged_out_create_subscription_returns_401(app_client):
    r = app_client.post(
        "/api/payments/subscriptions",
        json={"product_id": "basic_monthly", "expected_amount": 19900, "source": "pricing"},
    )
    assert r.status_code in (401, 403), r.text
    _assert_auth_error_body(r.text)


def test_logged_out_change_subscription_returns_401(app_client):
    r = app_client.post(
        "/api/payments/subscriptions/change",
        json={"to_product_id": "max_monthly", "expected_amount": 49900},
    )
    assert r.status_code in (401, 403), r.text
    _assert_auth_error_body(r.text)


def test_logged_out_cancel_subscription_returns_401(app_client):
    r = app_client.post(
        "/api/payments/subscriptions/cancel",
        json={"cancel_at_cycle_end": False},
    )
    assert r.status_code in (401, 403), r.text
    _assert_auth_error_body(r.text)


def test_logged_out_verify_order_returns_401(app_client):
    r = app_client.post(
        "/api/payments/orders/verify",
        json={
            "payment_id": "zk_pack_phantom",
            "razorpay_payment_id": "pay_x",
            "razorpay_order_id": "order_x",
            "razorpay_signature": "deadbeef" * 8,
        },
    )
    assert r.status_code in (401, 403), r.text
    _assert_auth_error_body(r.text)


def test_logged_out_subscription_me_returns_401(app_client):
    """``GET /api/payments/subscriptions/me`` is also gated — the launcher's
    ``fetchMySubscription`` checks for 401 and degrades to ``{subscription:null}``;
    without the 401 it would 500 and break the pricing page."""
    r = app_client.get("/api/payments/subscriptions/me")
    assert r.status_code in (401, 403), r.text
    _assert_auth_error_body(r.text)


# ─────────────────────────── bad-token denial ───────────────────────────


def test_garbage_bearer_token_returns_401(app_client):
    """Tampered / random bearer token must also 401 cleanly (not 500)."""
    r = app_client.post(
        "/api/payments/orders",
        json={"product_id": "zettel-pack-100"},
        headers={"Authorization": "Bearer not-a-real-jwt-just-random-string"},
    )
    assert r.status_code in (401, 403), r.text
    _assert_auth_error_body(r.text)
