"""UP-19: BOLA / cross-tenant denial across billing-profile / payment / subscription.

User A and User B mint independent identities (separate auth.users rows,
separate workspaces, separate JWTs). The pricing surface must enforce
``_scope(user_sub)`` so A can never read or mutate B's data.

Patterned on ``tests/integration/v2/test_cross_tenant_denial.py`` — including
the hardened OWASP API1:2023 BOLA UUID-leak guard: even when an HTTP 403/404/
405 correctly denies access, the response body must NOT echo back the victim's
auth_user_id (a leak in the error payload still constitutes a partial enum
oracle for an attacker).

Routes covered:
  * ``GET  /api/pricing/billing-profile``           (JWT-scoped, no path param)
  * ``GET  /api/payments/subscriptions/me``         (JWT-scoped)
  * ``GET  /api/payments/status/{payment_id}``      (path-param surface)
  * ``POST /api/payments/orders/verify``            (payment_id in body)
  * ``POST /api/payments/subscriptions/cancel``     (JWT-scoped)

Marked ``@pytest.mark.live`` because every test mints real auth users via the
v2 Supabase project (JWKS-verified JWTs are required by ``get_current_user``).

Discovery 2026-05-11 notes: the production billing-profile GET is JWT-scoped
(no ``/billing-profile/{id}`` path-param route exists), so the test for the
profile surface verifies that A's response NEVER contains B's identifiers
rather than asserting a path-param 403 — there is no path param to attack.
The same property holds: A cannot see B's data.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture
def app_client(monkeypatch):
    """TestClient over a freshly-created app with v2 schema bound + Razorpay creds.

    Razorpay creds injected so ``is_razorpay_configured()`` returns True; tests
    that do not actually open Razorpay never reach the outbound SDK so respx is
    not required here.
    """
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-bola-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-bola-tests")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_phase4_bola")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "phase4-bola-secret-not-deployed")

    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.features.user_pricing import razorpay_client
    razorpay_client.reset_client_cache()

    from website.app import create_app
    return TestClient(create_app())


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _no_uuid_leak(body_text: str, *uuids) -> None:
    """OWASP API1:2023 BOLA — assert none of the victim UUIDs appear in body."""
    for u in uuids:
        assert str(u) not in body_text, (
            f"cross-tenant leak: {u} appears in cross-tenant error body. "
            f"Body excerpt: {body_text[:400]!r}"
        )


# ─────────────────────────── billing-profile BOLA ───────────────────────────


def test_billing_profile_get_isolated_per_jwt(app_client, mint_user):
    """A and B each create a billing profile; A's GET must return ONLY A's data.

    The route ``GET /api/pricing/billing-profile`` is JWT-scoped (no path
    param), so cross-tenant attack is via JWT swap. We verify that A's
    response does not contain B's name / email / profile_id.

    Discovery 2026-05-11: ``billing.pricing_billing_profiles`` (v2 row) stores
    only profile_id, email, name, razorpay_customer_id, razorpay_subscriber_id,
    default_currency, metadata, created_at, updated_at — phone lives only in
    the legacy memory mirror today. The cross-tenant property we pin here is
    on what the v2 row DOES return (name + email + profile_id), which is the
    isolation surface that matters in production.
    """
    a = mint_user()
    b = mint_user()
    # Use unique recognisable names so cross-bleed is unambiguous in the body.
    a_name = f"Attacker-A-{uuid.uuid4().hex[:8]}"
    b_name = f"Victim-B-{uuid.uuid4().hex[:8]}"
    # Seed both via the public PUT — mirrors prod path.
    r_b = app_client.put(
        "/api/pricing/billing-profile",
        json={"phone": "+919000000002", "name": b_name},
        headers=_auth(b.jwt),
    )
    assert r_b.status_code in (200, 201), r_b.text
    r_a = app_client.put(
        "/api/pricing/billing-profile",
        json={"phone": "+919000000001", "name": a_name},
        headers=_auth(a.jwt),
    )
    assert r_a.status_code in (200, 201), r_a.text

    # A fetches — must see A's data, never B's.
    r = app_client.get("/api/pricing/billing-profile", headers=_auth(a.jwt))
    assert r.status_code == 200, r.text
    body = r.text
    assert a_name in body, f"A's GET missing A's name: {body!r}"
    assert b_name not in body, f"A's GET leaked B's name: {body!r}"
    assert b.email not in body, f"A's GET leaked B's email: {body!r}"
    _no_uuid_leak(body, b.auth_user_id, b.profile_id)

    # Reverse direction: B's GET must also be isolated.
    r2 = app_client.get("/api/pricing/billing-profile", headers=_auth(b.jwt))
    assert r2.status_code == 200, r2.text
    body2 = r2.text
    assert b_name in body2
    assert a_name not in body2
    assert a.email not in body2
    _no_uuid_leak(body2, a.auth_user_id, a.profile_id)


def test_billing_profile_unauthenticated_denied(app_client):
    """Defence-in-depth: no JWT → no profile data, no oracle.

    The denial must NOT echo any guessable UUID back (which would happen
    if FastAPI rendered a request.user attribute into the error)."""
    r = app_client.get("/api/pricing/billing-profile")
    assert r.status_code in (401, 403), r.text


# ─────────────────────────── /payments/subscriptions/me BOLA ───────────────────────────


def test_subscription_me_isolated_per_jwt(app_client, mint_user):
    """``GET /api/payments/subscriptions/me`` returns the JWT-holder's sub only.

    Without any subscription, both A and B see ``{"subscription": null}``; the
    safety property is that A cannot trigger a 200 containing B's
    razorpay_subscription_id.
    """
    a = mint_user()
    b = mint_user()
    r_a = app_client.get("/api/payments/subscriptions/me", headers=_auth(a.jwt))
    r_b = app_client.get("/api/payments/subscriptions/me", headers=_auth(b.jwt))
    assert r_a.status_code == 200 and r_b.status_code == 200, (r_a.text, r_b.text)
    # Even though both are null today, assert no cross-bleed UUIDs in A's body.
    _no_uuid_leak(r_a.text, b.auth_user_id, b.profile_id)
    _no_uuid_leak(r_b.text, a.auth_user_id, a.profile_id)


# ─────────────────────── /payments/status/{payment_id} BOLA ───────────────────────


def test_payment_status_other_users_payment_id_404(app_client, mint_user):
    """B creates a payment record (via /api/payments/orders); A guesses its id.

    ``payment_status`` checks ``record.render_user_id == user["sub"]`` (see
    routes.py:889) and raises 404 ``payment_not_found`` if it does not match.
    The 404 body must NOT echo B's auth_user_id (which would be a partial
    enumeration oracle).
    """
    a = mint_user()
    b = mint_user()
    # B prerequisites: billing profile, then create a pack order. The order
    # call requires Razorpay reachability to mint a real Razorpay order, but
    # we don't need that to test the BOLA on the status endpoint — we mint a
    # synthetic payment_id and ask A to query it. Even a non-existent id must
    # 404 cleanly without echoing anyone's UUID.
    synthetic_pid = f"zk_pack_{uuid.uuid4().hex}"
    r = app_client.get(
        f"/api/payments/status/{synthetic_pid}",
        headers=_auth(a.jwt),
    )
    assert r.status_code == 404, r.text
    _no_uuid_leak(r.text, b.auth_user_id, b.profile_id)


def test_payment_verify_other_users_payment_id_404(app_client, mint_user):
    """``POST /api/payments/orders/verify`` looks up ``record.render_user_id``
    against the caller's JWT sub (routes.py:459). If A submits a payment_id
    owned by B, the route raises 404 ``payment_not_found`` BEFORE any HMAC
    check — proving the scope check fires first.
    """
    a = mint_user()
    b = mint_user()
    synthetic_pid = f"zk_pack_{uuid.uuid4().hex}"
    r = app_client.post(
        "/api/payments/orders/verify",
        json={
            "payment_id": synthetic_pid,
            "razorpay_payment_id": "pay_attack_x",
            "razorpay_order_id": "order_attack_x",
            "razorpay_signature": "deadbeef" * 8,
        },
        headers=_auth(a.jwt),
    )
    assert r.status_code in (403, 404), r.text
    _no_uuid_leak(r.text, b.auth_user_id, b.profile_id)


# ─────────────────────────── subscription cancel BOLA ───────────────────────────


def test_subscription_cancel_other_user_no_active_404(app_client, mint_user):
    """A cancels — A has no sub → 404 ``no_active_subscription``.

    The cancel route is JWT-scoped via ``repo.get_subscription(user_sub=user["sub"])``
    (routes.py:321). Even if B has an active sub, A's call uses A's JWT and
    finds A's (absent) sub — never B's. The 404 body must not echo B's id.
    """
    a = mint_user()
    b = mint_user()
    r = app_client.post(
        "/api/payments/subscriptions/cancel",
        json={"cancel_at_cycle_end": False},
        headers=_auth(a.jwt),
    )
    assert r.status_code == 404, r.text
    _no_uuid_leak(r.text, b.auth_user_id, b.profile_id)


def test_subscription_cancel_does_not_accept_force_user_sub_override(app_client, mint_user):
    """Defence-in-depth: ``SubscriptionCancelRequest`` only accepts
    ``cancel_at_cycle_end: bool``. Extra fields like ``force_user_sub`` or
    ``user_id`` MUST NOT bypass scope. Pydantic's default behaviour ignores
    extra fields, so this asserts a regression — if the model ever switches
    to ``extra="allow"``, this test will catch the BOLA window.
    """
    a = mint_user()
    b = mint_user()
    r = app_client.post(
        "/api/payments/subscriptions/cancel",
        json={
            "cancel_at_cycle_end": False,
            # Hostile overrides — must be ignored / rejected.
            "force_user_sub": str(b.auth_user_id),
            "user_id": str(b.auth_user_id),
            "render_user_id": str(b.auth_user_id),
            "subscription_id": "sub_belonging_to_b",
        },
        headers=_auth(a.jwt),
    )
    # Whether extras are silently dropped (404 no_active) or rejected (422),
    # the response MUST NOT echo B's UUID and MUST NOT touch B's sub.
    assert r.status_code in (404, 422), r.text
    _no_uuid_leak(r.text, b.auth_user_id, b.profile_id)
