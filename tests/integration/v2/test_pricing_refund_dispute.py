"""Phase 3 — UP-17 / UP-18: refund + dispute webhook lifecycles.

Locks two production invariants:

  UP-17  ``refund.processed`` webhook triggers ``record_refund`` +
         ``deduct_pack_credits``. A replay of the SAME ``event.id`` MUST
         short-circuit at the route-level ``event_already_processed``
         guard so credits are deducted at most once. A partial refund
         deducts proportionally to ``refund.amount / payment.amount``.

  UP-18  ``payment.dispute.*`` webhooks toggle the module-level
         ``_DISPUTE_FROZEN`` set (repository.py:747-755):
            created / under_review / action_required / lost → freeze
            won / closed                                    → unfreeze
         ``is_user_dispute_frozen`` mirrors the set; ``create_order``
         consults it for the 409 account_frozen gate (covered in
         test_pricing_mutations.py).

Webhook bodies are signed with HMAC-SHA256 using a per-test
``RAZORPAY_WEBHOOK_SECRET`` injected via ``monkeypatch``. Razorpay outbound
calls do not occur in these paths so no respx mocks are required.

Marked ``@pytest.mark.live`` — minting users hits the live v2 project.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture
def webhook_secret(monkeypatch) -> str:
    secret = "wave-a-refund-dispute-test-secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    from website.features.user_pricing import razorpay_client
    razorpay_client.reset_client_cache()
    return secret


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-refund-dispute-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-refund-dispute-tests")
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


def _post(client: TestClient, body_obj: dict, secret: str):
    body = json.dumps(body_obj).encode("utf-8")
    sig = _sign(body, secret)
    return client.post(
        "/api/payments/webhook",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )


def _seed_pack_payment(user_sub: str, *, quantity: int, amount_paise: int) -> dict:
    """Create + mark-paid a pack payment so refund handlers have a record.

    Returns the paid payment dict. Uses the real repository surface — no SQL
    pokes — so this seeds both the in-memory store and the v2 billing tables.
    """
    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    payment = repo.create_payment_record(
        user_sub=user_sub,
        product_id="zettel_5",
        kind="pack",
        amount=amount_paise,
        currency="INR",
        meter="zettel",
        quantity=quantity,
    )
    fake_order = f"order_phase3_{uuid.uuid4().hex[:10]}"
    repo.attach_provider_order(payment_id=payment["payment_id"], razorpay_order_id=fake_order)
    paid = repo.mark_payment_paid(
        payment_id=payment["payment_id"],
        razorpay_payment_id=f"pay_phase3_{uuid.uuid4().hex[:10]}",
        signature="phase3-test",
    )
    # Credit the wallet so subsequent refund deductions have something to
    # subtract from. Mirrors what ``_apply_fulfillment`` does in the verify
    # route (we avoid going through verify to keep the test focused).
    repo.add_pack_credits(user_sub=user_sub, meter="zettel", quantity=quantity)
    return paid


def _refund_event(
    *,
    event_id: str,
    refund_id: str,
    payment: dict,
    user_sub: str,
    refund_amount: int,
    event_type: str = "refund.processed",
    status_speed: str = "normal",
) -> dict:
    """Build a Razorpay refund.* event body matching the handler's payload shape.

    ``_h_refund_processed`` (routes.py:731-761) reads:
      payload["refund"]["entity"]["id"], .amount, .speed_processed
      payload["payment"]["entity"]["id"], .notes.payment_id, .notes.render_user_id
    """
    return {
        "event": event_type,
        "id": event_id,
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "amount": refund_amount,
                    "speed_processed": status_speed,
                    "payment_id": payment.get("razorpay_payment_id") or "",
                }
            },
            "payment": {
                "entity": {
                    "id": payment.get("razorpay_payment_id") or "",
                    "notes": {
                        "payment_id": payment["payment_id"],
                        "render_user_id": user_sub,
                    },
                }
            },
        },
    }


def _dispute_event(
    *,
    event_id: str,
    dispute_id: str,
    phase: str,
    user_sub: str,
    payment: dict | None = None,
    amount: int = 50000,
) -> dict:
    """Build a Razorpay payment.dispute.<phase> event body.

    ``_dispute_handler`` (routes.py:780-804) reads
    ``payload["payment.dispute"]["entity"]`` or ``payload["dispute"]["entity"]``.
    """
    notes: dict[str, Any] = {"render_user_id": user_sub}
    if payment:
        notes["payment_id"] = payment["payment_id"]
    return {
        "event": f"payment.dispute.{phase}",
        "id": event_id,
        "payload": {
            "payment.dispute": {
                "entity": {
                    "id": dispute_id,
                    "amount": amount,
                    "reason_code": "phase3_probe",
                    "payment_id": (payment or {}).get("razorpay_payment_id") or "",
                }
            },
            "payment": {
                "entity": {
                    "id": (payment or {}).get("razorpay_payment_id") or "",
                    "notes": notes,
                }
            },
        },
    }


# ─────────────────────── UP-17: refund + idempotency ───────────────────────


def test_refund_processed_deducts_pack_credits_proportionally(
    app_client, webhook_secret, mint_user
):
    """Half refund of a 10-zettel pack → 5 zettels deducted from the wallet.

    Locks the proportional-deduction math at routes.py:740-747.
    """
    user = mint_user()
    user_sub = str(user.auth_user_id)
    payment = _seed_pack_payment(user_sub, quantity=10, amount_paise=20000)

    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    before = repo.get_balances(user_sub=user_sub).get("zettel", 0)
    assert before == 10, f"seed sanity: expected 10 zettels, got {before}"

    event_id = f"evt_refund_proc_{uuid.uuid4().hex[:12]}"
    refund_id = f"rfnd_phase3_{uuid.uuid4().hex[:10]}"
    body = _refund_event(
        event_id=event_id,
        refund_id=refund_id,
        payment=payment,
        user_sub=user_sub,
        refund_amount=10000,  # half of 20000 paise
    )
    r = _post(app_client, body, webhook_secret)
    assert r.status_code == 200, f"webhook: {r.status_code} {r.text[:200]}"

    after = repo.get_balances(user_sub=user_sub).get("zettel", 0)
    assert after == 5, (
        f"proportional deduction wrong: 10 - (10/20*10) -> expected 5, got {after}"
    )


def test_refund_processed_replay_does_not_double_deduct(
    app_client, webhook_secret, mint_user
):
    """Same ``event.id`` posted twice → credits deducted exactly once.

    Locks the event-level idempotency via ``event_already_processed``
    (repository.py:759). Without this guard a Razorpay retry would
    double-debit the user's wallet.
    """
    user = mint_user()
    user_sub = str(user.auth_user_id)
    payment = _seed_pack_payment(user_sub, quantity=10, amount_paise=20000)

    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()

    event_id = f"evt_refund_replay_{uuid.uuid4().hex[:12]}"
    refund_id = f"rfnd_phase3_{uuid.uuid4().hex[:10]}"
    body = _refund_event(
        event_id=event_id,
        refund_id=refund_id,
        payment=payment,
        user_sub=user_sub,
        refund_amount=20000,  # full refund -> would deduct all 10 zettels
    )
    r1 = _post(app_client, body, webhook_secret)
    r2 = _post(app_client, body, webhook_secret)
    assert r1.status_code == 200, f"first: {r1.status_code} {r1.text[:200]}"
    assert r2.status_code == 200, f"replay: {r2.status_code} {r2.text[:200]}"
    assert r2.json().get("status") in {"duplicate", "ok"}, r2.json()

    after = repo.get_balances(user_sub=user_sub).get("zettel", 0)
    assert after == 0, (
        f"full refund applied once -> balance 0; got {after} (replay double-deducted)"
    )


def test_refund_failed_does_not_deduct(app_client, webhook_secret, mint_user):
    """``refund.failed`` records the refund row but MUST NOT touch the wallet.

    Negative control for the deduction path — locks the routing in
    ``_h_refund_failed`` (routes.py:764-777) which only calls
    ``record_refund``, never ``deduct_pack_credits``.
    """
    user = mint_user()
    user_sub = str(user.auth_user_id)
    payment = _seed_pack_payment(user_sub, quantity=10, amount_paise=20000)

    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    before = repo.get_balances(user_sub=user_sub).get("zettel", 0)

    body = _refund_event(
        event_id=f"evt_refund_failed_{uuid.uuid4().hex[:12]}",
        refund_id=f"rfnd_phase3_{uuid.uuid4().hex[:10]}",
        payment=payment,
        user_sub=user_sub,
        refund_amount=20000,
        event_type="refund.failed",
    )
    r = _post(app_client, body, webhook_secret)
    assert r.status_code == 200, f"webhook: {r.status_code} {r.text[:200]}"

    after = repo.get_balances(user_sub=user_sub).get("zettel", 0)
    assert after == before, (
        f"refund.failed wrongly touched wallet: {before} -> {after}"
    )


# ─────────────────────── UP-18: dispute lifecycle freeze toggle ───────────────────────


@pytest.mark.parametrize("freeze_phase", ["created", "under_review", "action_required", "lost"])
def test_dispute_freeze_phases_set_frozen_flag(
    app_client, webhook_secret, mint_user, freeze_phase
):
    """Every freeze-phase webhook flips ``is_user_dispute_frozen`` to True."""
    user = mint_user()
    user_sub = str(user.auth_user_id)

    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()
    assert repo.is_user_dispute_frozen(user_sub=user_sub) is False, "fresh user is not frozen"

    body = _dispute_event(
        event_id=f"evt_disp_{freeze_phase}_{uuid.uuid4().hex[:12]}",
        dispute_id=f"disp_phase3_{uuid.uuid4().hex[:10]}",
        phase=freeze_phase,
        user_sub=user_sub,
    )
    r = _post(app_client, body, webhook_secret)
    assert r.status_code == 200, f"webhook: {r.status_code} {r.text[:200]}"

    assert repo.is_user_dispute_frozen(user_sub=user_sub) is True, (
        f"phase={freeze_phase} did not freeze user_sub={user_sub}"
    )

    # Defensive teardown so a later test sharing the module-level set is clean.
    try:
        repo.record_dispute(
            razorpay_dispute_id=f"disp_cleanup_{uuid.uuid4().hex[:8]}",
            razorpay_payment_id=None,
            payment_id=None,
            render_user_id=user_sub,
            amount=0,
            phase="won",
            payload={},
        )
    except Exception:
        pass


@pytest.mark.parametrize("clear_phase", ["won", "closed"])
def test_dispute_clear_phases_unfreeze(app_client, webhook_secret, mint_user, clear_phase):
    """``won`` / ``closed`` webhooks unfreeze a previously-frozen user."""
    user = mint_user()
    user_sub = str(user.auth_user_id)

    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()

    # Freeze first via a created webhook so the test exercises the real toggle.
    freeze_body = _dispute_event(
        event_id=f"evt_disp_freeze_{uuid.uuid4().hex[:12]}",
        dispute_id=f"disp_phase3_{uuid.uuid4().hex[:10]}",
        phase="created",
        user_sub=user_sub,
    )
    r1 = _post(app_client, freeze_body, webhook_secret)
    assert r1.status_code == 200
    assert repo.is_user_dispute_frozen(user_sub=user_sub) is True

    clear_body = _dispute_event(
        event_id=f"evt_disp_{clear_phase}_{uuid.uuid4().hex[:12]}",
        dispute_id=f"disp_phase3_{uuid.uuid4().hex[:10]}",
        phase=clear_phase,
        user_sub=user_sub,
    )
    r2 = _post(app_client, clear_body, webhook_secret)
    assert r2.status_code == 200, f"webhook: {r2.status_code} {r2.text[:200]}"

    assert repo.is_user_dispute_frozen(user_sub=user_sub) is False, (
        f"phase={clear_phase} did not unfreeze user_sub={user_sub}"
    )


def test_dispute_lifecycle_created_then_lost_keeps_frozen(app_client, webhook_secret, mint_user):
    """created → lost: user must remain frozen (lost is itself a freeze phase)."""
    user = mint_user()
    user_sub = str(user.auth_user_id)

    from website.features.user_pricing.repository import get_pricing_repository
    repo = get_pricing_repository()

    for phase in ("created", "lost"):
        r = _post(
            app_client,
            _dispute_event(
                event_id=f"evt_disp_{phase}_{uuid.uuid4().hex[:12]}",
                dispute_id=f"disp_phase3_{uuid.uuid4().hex[:10]}",
                phase=phase,
                user_sub=user_sub,
            ),
            webhook_secret,
        )
        assert r.status_code == 200, f"{phase}: {r.status_code} {r.text[:200]}"

    assert repo.is_user_dispute_frozen(user_sub=user_sub) is True, (
        "created -> lost must leave user frozen (lost is a freeze-phase, not a clear-phase)"
    )

    # Cleanup so cross-test pollution is bounded.
    repo.record_dispute(
        razorpay_dispute_id=f"disp_cleanup_{uuid.uuid4().hex[:8]}",
        razorpay_payment_id=None,
        payment_id=None,
        render_user_id=user_sub,
        amount=0,
        phase="won",
        payload={},
    )
    assert repo.is_user_dispute_frozen(user_sub=user_sub) is False
