"""UP-06: webhook handler-matrix completeness — every entry in ``_WEBHOOK_HANDLERS``.

Auto-parametrized from ``_WEBHOOK_HANDLERS.keys()`` so adding/removing a
handler in production code automatically expands/contracts the matrix.
Discovery 2026-05-11 confirmed 26 keys (payments 4 · refunds 3 · disputes 6 ·
subscriptions 10 · invoices 3). The plan called for 22 — the registry is
authoritative; this test reflects current state.

Two coverage axes per event type:
  1. Happy path — payload contains the entity dicts each handler can
     plausibly read. We do NOT require ``201`` fulfillment, only that the
     route returns 2xx and never 5xx.
  2. Missing-payload — empty ``{}`` body. Razorpay retries 5xx → wasted
     retries; handlers must return 4xx or 2xx, never 500.

Plus a single unknown-subtype test (``subscription.galaxy_brain``) to lock
the permissive-on-receipt design.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from website.features.user_pricing.routes import _WEBHOOK_HANDLERS


pytestmark = pytest.mark.live


HANDLER_EVENTS = sorted(_WEBHOOK_HANDLERS.keys())


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture
def webhook_secret(monkeypatch) -> str:
    secret = "wave-a-handler-matrix-test-secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    from website.features.user_pricing import razorpay_client
    razorpay_client.reset_client_cache()
    return secret


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-webhook-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-webhook-tests")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.app import create_app
    return TestClient(create_app())


# ─────────────────────────── helpers ───────────────────────────


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(client: TestClient, event_body: dict, secret: str):
    body = json.dumps(event_body).encode("utf-8")
    sig = _sign(body, secret)
    return client.post(
        "/api/payments/webhook",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )


def _build_payload(user_sub: str) -> dict:
    pay_id = f"pay_{uuid.uuid4().hex[:10]}"
    sub_id = f"sub_{uuid.uuid4().hex[:10]}"
    refund_id = f"rfnd_{uuid.uuid4().hex[:10]}"
    order_id = f"ord_{uuid.uuid4().hex[:10]}"
    inv_id = f"inv_{uuid.uuid4().hex[:10]}"
    notes = {"payment_id": pay_id, "user_sub": user_sub}
    return {
        "payment": {"entity": {"id": pay_id, "notes": notes, "order_id": order_id}},
        "subscription": {"entity": {"id": sub_id, "notes": notes}},
        "refund": {
            "entity": {
                "id": refund_id,
                "payment_id": pay_id,
                "notes": notes,
            }
        },
        "order": {"entity": {"id": order_id, "notes": notes}},
        "invoice": {"entity": {"id": inv_id, "subscription_id": sub_id, "notes": notes}},
    }


# ─────────────────────────── tests ───────────────────────────


def test_handler_registry_count_locked():
    """Discovery 2026-05-11: 26 handler keys. If this changes, the matrix
    auto-expands, but the count assertion ensures a deliberate review.
    """
    assert len(HANDLER_EVENTS) == 26, (
        f"_WEBHOOK_HANDLERS has {len(HANDLER_EVENTS)} keys; expected 26. "
        f"If you added/removed a handler intentionally, update this assertion "
        f"and confirm the matrix below still covers the new key. Keys: "
        f"{HANDLER_EVENTS}"
    )


@pytest.mark.parametrize("event_type", HANDLER_EVENTS)
def test_handler_happy_path(app_client, webhook_secret, mint_user, event_type):
    """Every handler must accept a realistic payload without 5xx."""
    user = mint_user()
    event = {
        "event": event_type,
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "payload": _build_payload(str(user.auth_user_id)),
    }
    r = _post(app_client, event, webhook_secret)
    assert r.status_code in (200, 202), (
        f"{event_type}: {r.status_code} {r.text[:300]}"
    )


@pytest.mark.parametrize("event_type", HANDLER_EVENTS)
def test_handler_missing_payload_no_5xx(app_client, webhook_secret, event_type):
    """Empty payload must yield 2xx or 4xx — never 5xx (avoid Razorpay retry-storm)."""
    event = {
        "event": event_type,
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "payload": {},
    }
    r = _post(app_client, event, webhook_secret)
    assert r.status_code < 500, (
        f"{event_type} 5xx on empty payload: {r.status_code} {r.text[:300]}"
    )


def test_unknown_event_subtype_no_5xx(app_client, webhook_secret):
    """Unknown event types are logged + recorded (permissive on receipt)."""
    event = {
        "event": "subscription.galaxy_brain",
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "payload": {},
    }
    r = _post(app_client, event, webhook_secret)
    assert r.status_code in (200, 202), (
        f"unknown subtype: {r.status_code} {r.text[:300]}"
    )
    assert r.json().get("status") == "ignored"
