"""UP-22: pricing response shapers (``_public_payment``, ``_public_subscription``,
``_checkout_payload``) must NOT leak secrets, internal notes, or raw provider
errors when given hostile / over-broad inputs.

The route handlers thread the raw repository row (which can contain provider-
response detritus from a defensive ``mark_payment_paid`` upsert) through these
shapers. Anything that escapes the explicit allowlist of fields a shaper
returns is a candidate leak — these tests pin the allowlists.

The launcher's UP-21 gate covers the BROWSER side (no secret in shipped JS).
This test covers the API side: even if a leaky row enters the shaper, the
shaper's response stays clean.
"""
from __future__ import annotations

from website.features.user_pricing.routes import (
    _checkout_payload,
    _public_payment,
    _public_subscription,
)


# ─────────────────────────── _public_payment ───────────────────────────


def test_public_payment_strips_unknown_secret_fields():
    """A hostile/over-broad record must not surface secrets through the shaper.

    Even if the repository row gains a 'key_secret', 'razorpay_response', or
    'notes' attribute (e.g. via a future code path that stores the full
    Razorpay payment.entity), the public response stays on the allowlist.
    """
    raw = {
        "payment_id": "zk_pack_1234",
        "status": "paid",
        "kind": "pack",
        "amount": 9900,
        "currency": "INR",
        "product_id": "zettel-pack-100",
        "razorpay_payment_id": "pay_pub_1",
        "razorpay_order_id": "order_pub_1",
        "razorpay_subscription_id": None,
        "paid_at": "2026-05-11T00:00:00Z",
        # Hostile / leak-candidate fields below — none must survive.
        "key_secret": "rzp_secret_leak",
        "razorpay_key_secret": "rzp_secret_leak",
        "webhook_secret": "wh_secret_leak",
        "notes": {"internal_user_sub": "must-not-leak", "render_user_id": "must-not-leak"},
        "razorpay_response": {"error": {"raw": "boom — Internal stack trace"}},
        "render_user_id": "auth-user-uuid-leak",
        "signature": "raw-hmac-bytes",
    }
    out = _public_payment(raw)
    flat = repr(out).lower()

    # No secret-shaped strings.
    assert "secret" not in flat, f"secret survived in: {out!r}"
    # No internal notes / scope-bound user ids.
    assert "internal_user_sub" not in flat
    assert "render_user_id" not in flat
    assert "must-not-leak" not in flat
    assert "auth-user-uuid-leak" not in flat
    # No raw provider error blob.
    assert "internal stack trace" not in flat
    assert "razorpay_response" not in flat
    # Allowlist intact.
    assert out["payment_id"] == "zk_pack_1234"
    assert out["status"] == "paid"
    assert out["razorpay_payment_id"] == "pay_pub_1"


def test_public_payment_returns_only_allowlisted_keys():
    """Explicit allowlist regression — adding a new internal field to the row
    must not auto-leak it. The shaper returns a fixed set of keys."""
    raw = {
        "payment_id": "zk_pack_x",
        "status": "paid",
        "kind": "pack",
        "amount": 100,
        "currency": "INR",
        "product_id": "x",
        "razorpay_payment_id": "p",
        "razorpay_order_id": "o",
        "razorpay_subscription_id": None,
        "paid_at": None,
        "extra_internal_column_added_later": "leak",
    }
    out = _public_payment(raw)
    expected = {
        "payment_id", "status", "kind", "amount", "currency", "product_id",
        "razorpay_payment_id", "razorpay_order_id", "razorpay_subscription_id",
        "paid_at",
    }
    assert set(out.keys()) == expected, (
        f"_public_payment leaked unexpected keys: {set(out.keys()) - expected}"
    )


# ─────────────────────────── _public_subscription ───────────────────────────


def test_public_subscription_strips_secrets_and_internal_ids():
    raw = {
        "plan_id": "basic",
        "period_id": "basic_monthly",
        "status": "active",
        "current_period_start": "2026-05-01",
        "current_period_end": "2026-06-01",
        "cancelled_at": None,
        "razorpay_subscription_id": "sub_pub_1",
        # Leak candidates.
        "plan_secret": "x",
        "key_secret": "y",
        "razorpay_webhook_secret": "z",
        "notes": {"private": "must-not-leak"},
        "render_user_id": "auth-user-uuid-leak",
    }
    out = _public_subscription(raw)
    flat = repr(out).lower()
    assert "secret" not in flat
    assert "render_user_id" not in flat
    assert "auth-user-uuid-leak" not in flat
    assert "must-not-leak" not in flat
    # Allowlist intact.
    assert out["plan_id"] == "basic"
    assert out["razorpay_subscription_id"] == "sub_pub_1"


def test_public_subscription_returns_only_allowlisted_keys():
    raw = {
        "plan_id": "max",
        "period_id": "max_yearly",
        "status": "active",
        "current_period_start": None,
        "current_period_end": None,
        "cancelled_at": None,
        "razorpay_subscription_id": "sub_x",
        "next_internal_column": "leak",
    }
    out = _public_subscription(raw)
    expected = {
        "plan_id", "period_id", "status",
        "current_period_start", "current_period_end",
        "cancelled_at", "razorpay_subscription_id",
    }
    assert set(out.keys()) == expected


# ─────────────────────────── _checkout_payload ───────────────────────────


def test_checkout_payload_never_embeds_key_secret():
    """``_checkout_payload`` builds the response the launcher uses to open
    Razorpay. It MUST contain the public ``key_id`` (Razorpay client side)
    and MUST NOT contain ``key_secret`` or any webhook secret.
    """
    product = {
        "id": "zettel-pack-100",
        "name": "Zettel Pack 100",
        "amount": 9900,
        "meter": "zettels",
        "quantity": 100,
        "kind": "pack",
    }
    user = {
        "sub": "auth-user-uuid-do-not-leak",
        "email": "buyer@example.com",
        "user_metadata": {"full_name": "Buyer Name"},
    }
    profile = {"phone": "+919999999999", "name": "Buyer", "email": "buyer@example.com"}

    out = _checkout_payload(
        payment_id="zk_pack_payment_xx",
        amount=9900,
        kind="pack",
        product=product,
        user=user,
        profile=profile,
        order_id="order_test_xx",
    )
    flat = repr(out).lower()

    # SAFETY: secret/webhook material must never appear.
    assert "key_secret" not in flat, f"key_secret leaked: {out!r}"
    assert "razorpay_key_secret" not in flat
    assert "webhook_secret" not in flat
    assert "razorpay_webhook_secret" not in flat

    # CONTRACT: public key_id must be present so the launcher can open Razorpay.
    assert "key_id" in out, f"key_id missing from checkout payload: {out!r}"


def test_checkout_payload_does_not_echo_jwt_or_auth_user_sub_into_top_level():
    """Defence-in-depth: the user dict carries the auth_user_sub from JWT.
    The payload purposely echoes it into ``notes`` for Razorpay webhook
    correlation — that is internal-to-payments routing data, NOT secret.
    But the auth_user_sub must not appear in any user-facing label or in
    the prefill block (which the browser DevTools can read).
    """
    product = {"id": "basic_monthly", "name": "Basic", "amount": 19900, "kind": "subscription"}
    user = {
        "sub": "user-sub-jwt-claim-do-not-leak-as-label",
        "email": "user@example.com",
        "user_metadata": {},
    }
    profile = {"phone": "+919999999999", "name": "User", "email": "user@example.com"}

    out = _checkout_payload(
        payment_id="zk_subscription_yy",
        amount=19900,
        kind="subscription",
        product=product,
        user=user,
        profile=profile,
        subscription_id="sub_yy",
    )

    # Prefill (visible in DevTools) must NOT include the JWT sub.
    prefill_flat = repr(out.get("prefill") or {})
    assert "user-sub-jwt-claim-do-not-leak-as-label" not in prefill_flat, (
        f"auth user sub leaked into checkout prefill: {prefill_flat}"
    )
    # Visible top-level labels (description/name) must not echo the sub.
    assert "user-sub-jwt-claim-do-not-leak-as-label" not in str(out.get("description", ""))
    assert "user-sub-jwt-claim-do-not-leak-as-label" not in str(out.get("name", ""))
