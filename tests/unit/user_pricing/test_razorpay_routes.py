"""Tests for the Razorpay integration in website/features/user_pricing.

The Razorpay SDK is mocked end-to-end — no real network calls are made.
Tests cover order creation (pack + subscription), signature verification
(success / mismatch / missing fields), the webhook handler (signature gate
+ idempotency + fulfillment), and authentication failures.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from website.features.user_pricing import razorpay_client, repository, routes


TEST_KEY_ID = "rzp_test_dummy_key_id"
TEST_KEY_SECRET = "test_secret_for_unit_tests_only"
TEST_WEBHOOK_SECRET = "test_webhook_secret"


def _hmac_sha256(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _razorpay_env(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", TEST_KEY_SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    # Force in-memory repo path even if a developer .env has live Supabase
    # creds — these tests must not touch any external DB. Phase 8.0.6 retired
    # supabase_kg.client; repository now re-exports v2's is_v2_configured as
    # is_supabase_configured for backward compatibility (see repository.py).
    from website.core.supabase_v2 import client as sb_client
    monkeypatch.setattr(sb_client, "is_v2_configured", lambda: False)
    monkeypatch.setattr(repository, "is_supabase_configured", lambda: False)
    razorpay_client.reset_client_cache()
    repository.reset_memory_state_for_tests()
    yield
    razorpay_client.reset_client_cache()
    repository.reset_memory_state_for_tests()


@pytest.fixture
def stub_user() -> dict:
    return {
        "sub": "user-test-1",
        "email": "buyer@example.com",
        "user_metadata": {"full_name": "Test Buyer"},
    }


@pytest.fixture
def saved_profile(stub_user) -> dict:
    repo = repository.get_pricing_repository()
    return repo.upsert_billing_profile(
        user_sub=stub_user["sub"],
        email=stub_user["email"],
        phone="9999999999",
        name="Test Buyer",
    )


class _FakeOrderResource:
    def __init__(self, order_id: str = "order_LIVE_TEST_1") -> None:
        self.order_id = order_id
        self.last_data: dict[str, Any] | None = None

    def create(self, *, data: dict[str, Any]) -> dict[str, Any]:
        self.last_data = data
        return {
            "id": self.order_id,
            "amount": data["amount"],
            "currency": data["currency"],
            "receipt": data.get("receipt"),
            "notes": data.get("notes"),
            "status": "created",
        }


class _FakePlanResource:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create(self, *, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return {"id": f"plan_FAKE_{len(self.created)}", "item": data["item"], "period": data["period"], "interval": data["interval"]}


class _FakeSubscriptionResource:
    def __init__(self, sub_id: str = "sub_FAKE_1") -> None:
        self.sub_id = sub_id
        self.created: list[dict[str, Any]] = []
        self.cancel_calls: list[tuple[str, dict[str, Any]]] = []

    def create(self, *, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return {"id": self.sub_id, **data, "status": "created"}

    def cancel(self, sub_id: str, opts: dict[str, Any]) -> dict[str, Any]:
        self.cancel_calls.append((sub_id, opts))
        return {"id": sub_id, "status": "cancelled"}


class _FakeRazorpayClient:
    def __init__(self, order_id: str = "order_LIVE_TEST_1", sub_id: str = "sub_FAKE_1") -> None:
        self.order = _FakeOrderResource(order_id)
        self.plan = _FakePlanResource()
        self.subscription = _FakeSubscriptionResource(sub_id)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeRazorpayClient()

    def _factory():
        return client

    monkeypatch.setattr(routes, "get_razorpay_client", _factory)
    # razorpay_client.get_or_create_plan calls get_razorpay_client itself —
    # patch it there too so the fake plan resource is used.
    from website.features.user_pricing import razorpay_client as rc
    monkeypatch.setattr(rc, "get_razorpay_client", _factory)
    return client


# ──────────────────────────────── orders ────────────────────────────────


@pytest.mark.asyncio
async def test_create_pack_order_returns_checkout_payload(stub_user, saved_profile, fake_client):
    payload = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )

    assert payload["kind"] == "pack"
    assert payload["key_id"] == TEST_KEY_ID
    assert payload["order_id"] == "order_LIVE_TEST_1"
    assert payload["amount"] == 9900
    assert payload["currency"] == "INR"
    assert payload["payment_id"].startswith("zk_pack_")
    assert payload["prefill"]["contact"] == "9999999999"
    assert payload["prefill"]["email"] == "buyer@example.com"

    assert fake_client.order.last_data["amount"] == 9900
    assert fake_client.order.last_data["notes"]["render_user_id"] == stub_user["sub"]
    assert fake_client.order.last_data["notes"]["product_id"] == "zettel_10"

    record = repository.get_pricing_repository().get_payment_record(payment_id=payload["payment_id"])
    assert record["razorpay_order_id"] == "order_LIVE_TEST_1"
    assert record["status"] == "created"


@pytest.mark.asyncio
async def test_create_subscription_creates_plan_and_razorpay_subscription(stub_user, saved_profile, fake_client):
    payload = await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )

    assert payload["kind"] == "subscription"
    assert payload["amount"] == 14900
    assert payload["subscription_id"] == "sub_FAKE_1"
    assert payload["recurring"] is True
    assert "order_id" not in payload  # subscription mode, not order mode

    # Plan was created exactly once with the right (period, amount).
    assert len(fake_client.plan.created) == 1
    plan_data = fake_client.plan.created[0]
    assert plan_data["period"] == "monthly"
    assert plan_data["interval"] == 1
    assert plan_data["item"]["amount"] == 14900

    # Subscription was created against that plan with notes for webhook routing.
    assert len(fake_client.subscription.created) == 1
    sub_data = fake_client.subscription.created[0]
    assert sub_data["plan_id"].startswith("plan_FAKE_")
    assert sub_data["total_count"] == 24  # monthly × 24 cycles
    assert sub_data["notes"]["render_user_id"] == stub_user["sub"]
    assert sub_data["notes"]["plan_id"] == "basic"


@pytest.mark.asyncio
async def test_create_subscription_quarterly_uses_interval_3(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_quarterly", expected_amount=39900),
        stub_user,
    )
    plan_data = fake_client.plan.created[0]
    assert plan_data["period"] == "monthly"
    assert plan_data["interval"] == 3


@pytest.mark.asyncio
async def test_create_subscription_yearly_uses_yearly_period(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="max_yearly", expected_amount=349900),
        stub_user,
    )
    plan_data = fake_client.plan.created[0]
    assert plan_data["period"] == "yearly"
    assert plan_data["interval"] == 1


@pytest.mark.asyncio
async def test_create_subscription_caches_plan_id_for_same_amount(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    # Cancel first sub so the second create is allowed.
    repo = repository.get_pricing_repository()
    sub = repo.get_subscription(user_sub=stub_user["sub"])
    repo.update_subscription_status(
        razorpay_subscription_id=sub["razorpay_subscription_id"], status="cancelled"
    )
    fake_client.subscription.sub_id = "sub_FAKE_2"
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    # Plan was created only once — second call hit the cache.
    assert len(fake_client.plan.created) == 1


@pytest.mark.asyncio
async def test_create_subscription_blocked_when_one_already_active(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    # Mark active to mimic post-activation state.
    repo = repository.get_pricing_repository()
    sub = repo.get_subscription(user_sub=stub_user["sub"])
    repo.update_subscription_status(
        razorpay_subscription_id=sub["razorpay_subscription_id"], status="active"
    )
    with pytest.raises(routes.HTTPException) as exc:
        await routes.create_subscription(
            routes.PaymentCreateRequest(product_id="max_monthly", expected_amount=34900),
            stub_user,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "subscription_already_active"


@pytest.mark.asyncio
async def test_cancel_subscription_calls_razorpay_and_marks_cancelled(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    result = await routes.cancel_subscription(
        routes.SubscriptionCancelRequest(cancel_at_cycle_end=False),
        stub_user,
    )
    assert result["status"] == "cancelled"
    assert fake_client.subscription.cancel_calls == [("sub_FAKE_1", {"cancel_at_cycle_end": 0})]
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "cancelled"
    assert sub.get("cancelled_at")


@pytest.mark.asyncio
async def test_cancel_at_cycle_end_marks_pending_cancel(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    result = await routes.cancel_subscription(
        routes.SubscriptionCancelRequest(cancel_at_cycle_end=True),
        stub_user,
    )
    assert result["status"] == "pending_cancel"
    assert fake_client.subscription.cancel_calls[-1][1] == {"cancel_at_cycle_end": 1}


@pytest.mark.asyncio
async def test_cancel_returns_404_when_no_subscription(stub_user, fake_client):
    with pytest.raises(routes.HTTPException) as exc:
        await routes.cancel_subscription(
            routes.SubscriptionCancelRequest(),
            stub_user,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "no_active_subscription"


@pytest.mark.asyncio
async def test_change_subscription_cancels_old_and_creates_new(stub_user, saved_profile, fake_client):
    # Start on basic_monthly and activate it.
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    repo = repository.get_pricing_repository()
    repo.update_subscription_status(razorpay_subscription_id="sub_FAKE_1", status="active")

    # Upgrade to max_monthly. The next subscription.create() should return a new id.
    fake_client.subscription.sub_id = "sub_FAKE_2"
    payload = await routes.change_subscription(
        routes.SubscriptionChangeRequest(to_product_id="max_monthly"),
        stub_user,
    )

    assert payload["kind"] == "subscription"
    assert payload["subscription_id"] == "sub_FAKE_2"
    assert fake_client.subscription.cancel_calls == [("sub_FAKE_1", {"cancel_at_cycle_end": 0})]

    new_sub = repo.get_subscription(user_sub=stub_user["sub"])
    assert new_sub["razorpay_subscription_id"] == "sub_FAKE_2"
    assert new_sub["plan_id"] == "max"
    # After the change, the new row has been written for sub_FAKE_2; lookups
    # for the old sub_id resolve to None thanks to the staleness guard so a
    # late webhook for the cancelled sub cannot corrupt the new row.
    assert repo.get_subscription_by_razorpay_id(razorpay_subscription_id="sub_FAKE_1") is None


@pytest.mark.asyncio
async def test_change_subscription_no_existing_falls_through_to_create(stub_user, saved_profile, fake_client):
    payload = await routes.change_subscription(
        routes.SubscriptionChangeRequest(to_product_id="basic_monthly"),
        stub_user,
    )
    assert payload["subscription_id"] == "sub_FAKE_1"
    assert fake_client.subscription.cancel_calls == []  # nothing to cancel


@pytest.mark.asyncio
async def test_change_subscription_blocks_same_active_plan(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    repository.get_pricing_repository().update_subscription_status(
        razorpay_subscription_id="sub_FAKE_1", status="active"
    )
    with pytest.raises(routes.HTTPException) as exc:
        await routes.change_subscription(
            routes.SubscriptionChangeRequest(to_product_id="basic_monthly"),
            stub_user,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "already_on_this_plan"
    assert fake_client.subscription.cancel_calls == []


@pytest.mark.asyncio
async def test_change_subscription_skips_cancel_for_cancelled_existing(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    repository.get_pricing_repository().update_subscription_status(
        razorpay_subscription_id="sub_FAKE_1", status="cancelled"
    )

    fake_client.subscription.sub_id = "sub_FAKE_2"
    payload = await routes.change_subscription(
        routes.SubscriptionChangeRequest(to_product_id="max_monthly"),
        stub_user,
    )
    assert payload["subscription_id"] == "sub_FAKE_2"
    assert fake_client.subscription.cancel_calls == []  # already cancelled, don't re-cancel


@pytest.mark.asyncio
async def test_change_subscription_rejects_free(stub_user, saved_profile, fake_client):
    with pytest.raises(routes.HTTPException) as exc:
        await routes.change_subscription(
            routes.SubscriptionChangeRequest(to_product_id="free"),
            stub_user,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] in {"free_not_purchasable", "invalid_product"}


@pytest.mark.asyncio
async def test_change_subscription_price_mismatch_409(stub_user, saved_profile, fake_client):
    with pytest.raises(routes.HTTPException) as exc:
        await routes.change_subscription(
            routes.SubscriptionChangeRequest(to_product_id="basic_monthly", expected_amount=1),
            stub_user,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "price_changed"


@pytest.mark.asyncio
async def test_my_subscription_returns_null_when_none(stub_user):
    payload = await routes.my_subscription(stub_user)
    assert payload == {"subscription": None}


@pytest.mark.asyncio
async def test_my_subscription_returns_public_view(stub_user, saved_profile, fake_client):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    payload = await routes.my_subscription(stub_user)
    assert payload["subscription"]["plan_id"] == "basic"
    assert payload["subscription"]["period_id"] == "basic_monthly"
    assert payload["subscription"]["razorpay_subscription_id"] == "sub_FAKE_1"


@pytest.mark.asyncio
async def test_create_order_requires_billing_profile(stub_user, fake_client):
    with pytest.raises(routes.HTTPException) as exc:
        await routes.create_order(
            routes.PaymentCreateRequest(product_id="zettel_10"),
            stub_user,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "billing_profile_required"


@pytest.mark.asyncio
async def test_create_order_rejects_amount_below_minimum(stub_user, saved_profile, fake_client, monkeypatch):
    monkeypatch.setattr(
        routes,
        "find_product",
        lambda product_id: {
            "kind": "pack",
            "id": "tiny_pack",
            "amount": 50,
            "meter": "zettel",
            "quantity": 1,
            "name": "Tiny",
        },
    )
    with pytest.raises(routes.HTTPException) as exc:
        await routes.create_order(
            routes.PaymentCreateRequest(product_id="tiny_pack", expected_amount=50),
            stub_user,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "amount_too_low"


@pytest.mark.asyncio
async def test_create_order_returns_503_when_razorpay_not_configured(stub_user, saved_profile, monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    razorpay_client.reset_client_cache()

    with pytest.raises(routes.HTTPException) as exc:
        await routes.create_order(
            routes.PaymentCreateRequest(product_id="zettel_10"),
            stub_user,
        )
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "payments_not_configured"


@pytest.mark.asyncio
async def test_create_order_marks_failed_on_provider_error(stub_user, saved_profile, monkeypatch):
    class BadClient:
        class _OrderRes:
            def create(self, *, data):
                raise RuntimeError("razorpay 5xx")
        order = _OrderRes()

    monkeypatch.setattr(routes, "get_razorpay_client", lambda: BadClient())

    with pytest.raises(routes.HTTPException) as exc:
        await routes.create_order(
            routes.PaymentCreateRequest(product_id="zettel_10"),
            stub_user,
        )
    assert exc.value.status_code == 502
    assert exc.value.detail["code"] == "payments_provider_error"


# ──────────────────────────── verify endpoint ────────────────────────────


@pytest.mark.asyncio
async def test_verify_order_success_marks_paid_and_credits_pack(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    payment_id = created["payment_id"]
    rzp_payment_id = "pay_DUMMY_12345"
    signature = _hmac_sha256(f"{created['order_id']}|{rzp_payment_id}", TEST_KEY_SECRET)

    result = await routes.verify_order(
        routes.PaymentVerifyRequest(
            payment_id=payment_id,
            razorpay_payment_id=rzp_payment_id,
            razorpay_order_id=created["order_id"],
            razorpay_signature=signature,
        ),
        stub_user,
    )

    assert result["status"] == "paid"
    record = repository.get_pricing_repository().get_payment_record(payment_id=payment_id)
    assert record["status"] == "paid"
    assert record["razorpay_payment_id"] == rzp_payment_id
    balances = repository.get_pricing_repository().get_balances(user_sub=stub_user["sub"])
    assert balances["zettel"] == 10


@pytest.mark.asyncio
async def test_verify_order_signature_mismatch_returns_400(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    with pytest.raises(routes.HTTPException) as exc:
        await routes.verify_order(
            routes.PaymentVerifyRequest(
                payment_id=created["payment_id"],
                razorpay_payment_id="pay_X",
                razorpay_order_id=created["order_id"],
                razorpay_signature="0" * 64,
            ),
            stub_user,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "signature_mismatch"
    record = repository.get_pricing_repository().get_payment_record(payment_id=created["payment_id"])
    assert record["status"] == "failed"


@pytest.mark.asyncio
async def test_verify_order_rejects_other_users_payment(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    intruder = {"sub": "user-other", "email": "intruder@example.com", "user_metadata": {}}
    with pytest.raises(routes.HTTPException) as exc:
        await routes.verify_order(
            routes.PaymentVerifyRequest(
                payment_id=created["payment_id"],
                razorpay_payment_id="pay_Y",
                razorpay_order_id=created["order_id"],
                razorpay_signature="abc",
            ),
            intruder,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "payment_not_found"


@pytest.mark.asyncio
async def test_verify_order_unknown_payment_returns_404(stub_user):
    with pytest.raises(routes.HTTPException) as exc:
        await routes.verify_order(
            routes.PaymentVerifyRequest(
                payment_id="zk_pack_does_not_exist",
                razorpay_payment_id="pay_Z",
                razorpay_order_id="order_Z",
                razorpay_signature="abc",
            ),
            stub_user,
        )
    assert exc.value.status_code == 404


# ──────────────────────────────── webhook ────────────────────────────────


class _FakeRequest:
    def __init__(self, body: bytes, signature: str) -> None:
        self._body = body
        self.headers = {"X-Razorpay-Signature": signature}

    async def body(self) -> bytes:
        return self._body


def _signed_request(event: dict) -> _FakeRequest:
    body = json.dumps(event).encode("utf-8")
    sig = hmac.new(TEST_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _FakeRequest(body, sig)


@pytest.mark.asyncio
async def test_webhook_payment_captured_credits_pack(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )

    event = {
        "id": "evt_test_001",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_RZP_001",
                    "notes": {
                        "payment_id": created["payment_id"],
                        "render_user_id": stub_user["sub"],
                        "product_id": "zettel_10",
                    },
                }
            }
        },
    }

    response = await routes.razorpay_webhook(_signed_request(event))

    assert response["status"] == "ok"
    record = repository.get_pricing_repository().get_payment_record(payment_id=created["payment_id"])
    assert record["status"] == "paid"
    assert record["razorpay_payment_id"] == "pay_RZP_001"
    balances = repository.get_pricing_repository().get_balances(user_sub=stub_user["sub"])
    assert balances["zettel"] == 10


@pytest.mark.asyncio
async def test_webhook_idempotent_on_duplicate_event(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    event = {
        "id": "evt_dup_001",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_RZP_DUP",
                    "notes": {"payment_id": created["payment_id"], "render_user_id": stub_user["sub"]},
                }
            }
        },
    }
    first = await routes.razorpay_webhook(_signed_request(event))
    second = await routes.razorpay_webhook(_signed_request(event))

    assert first["status"] == "ok"
    assert second["status"] == "duplicate"
    balances = repository.get_pricing_repository().get_balances(user_sub=stub_user["sub"])
    assert balances["zettel"] == 10  # not double-credited


@pytest.mark.asyncio
async def test_webhook_payment_failed_marks_record(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    event = {
        "id": "evt_failed_001",
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_FAIL",
                    "error_description": "INSUFFICIENT_FUNDS",
                    "notes": {"payment_id": created["payment_id"]},
                }
            }
        },
    }
    await routes.razorpay_webhook(_signed_request(event))
    record = repository.get_pricing_repository().get_payment_record(payment_id=created["payment_id"])
    assert record["status"] == "failed"
    assert record["failure_reason"] == "INSUFFICIENT_FUNDS"


@pytest.mark.asyncio
async def test_webhook_invalid_signature_returns_400(stub_user):
    body = json.dumps({"id": "evt_x", "event": "payment.captured"}).encode("utf-8")
    req = _FakeRequest(body, signature="0" * 64)
    with pytest.raises(routes.HTTPException) as exc:
        await routes.razorpay_webhook(req)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "invalid_signature"


@pytest.mark.asyncio
async def test_webhook_missing_event_fields_returns_400():
    event = {"id": "evt_only_id"}  # missing 'event'
    with pytest.raises(routes.HTTPException) as exc:
        await routes.razorpay_webhook(_signed_request(event))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "missing_event_fields"


@pytest.mark.asyncio
async def test_webhook_subscription_activated_creates_subscription(fake_client, stub_user, saved_profile):
    created = await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    event = {
        "id": "evt_sub_001",
        "event": "subscription.activated",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_FAKE_1",
                    "notes": {
                        "payment_id": created["payment_id"],
                        "render_user_id": stub_user["sub"],
                        "plan_id": "basic",
                        "period_id": "basic_monthly",
                        "months": "1",
                    },
                }
            },
            "payment": {"entity": {"id": "pay_RZP_SUB_001"}},
        },
    }
    await routes.razorpay_webhook(_signed_request(event))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub is not None
    assert sub["plan_id"] == "basic"
    assert sub["period_id"] == "basic_monthly"
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_webhook_unknown_event_records_but_ignores(stub_user):
    event = {"id": "evt_unknown_001", "event": "settlement.processed", "payload": {}}
    response = await routes.razorpay_webhook(_signed_request(event))
    assert response["status"] == "ignored"
    assert response["event"] == "settlement.processed"
    repo = repository.get_pricing_repository()
    assert repo.event_already_processed(event_id="evt_unknown_001")


def _sub_event(event_id: str, event_type: str, sub_id: str = "sub_FAKE_1", **notes_extra) -> dict:
    return {
        "id": event_id,
        "event": event_type,
        "payload": {
            "subscription": {
                "entity": {
                    "id": sub_id,
                    "notes": {**notes_extra},
                }
            }
        },
    }


@pytest.mark.asyncio
async def test_webhook_subscription_authenticated_marks_status(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    await routes.razorpay_webhook(_signed_request(_sub_event("evt_auth", "subscription.authenticated")))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "authenticated"


@pytest.mark.asyncio
async def test_webhook_subscription_charged_extends_paid_count(fake_client, stub_user, saved_profile):
    created = await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    event = {
        "id": "evt_charged_1",
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_FAKE_1",
                    "notes": {
                        "payment_id": created["payment_id"],
                        "render_user_id": stub_user["sub"],
                        "plan_id": "basic",
                        "period_id": "basic_monthly",
                        "months": "1",
                    },
                }
            },
            "payment": {"entity": {"id": "pay_RENEW_001"}},
        },
    }
    await routes.razorpay_webhook(_signed_request(event))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "active"
    assert sub.get("paid_count", 0) >= 1


@pytest.mark.asyncio
async def test_webhook_subscription_pending_marks_grace(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    await routes.razorpay_webhook(_signed_request(_sub_event("evt_pending", "subscription.pending")))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "grace"
    assert sub.get("failure_reason") == "payment_retry"


@pytest.mark.asyncio
async def test_webhook_subscription_halted(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    await routes.razorpay_webhook(_signed_request(_sub_event("evt_halt", "subscription.halted")))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "halted"


@pytest.mark.asyncio
async def test_webhook_subscription_paused_then_resumed(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    await routes.razorpay_webhook(_signed_request(_sub_event("evt_pause", "subscription.paused")))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "paused"
    await routes.razorpay_webhook(_signed_request(_sub_event("evt_resume", "subscription.resumed")))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_webhook_subscription_cancelled(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    await routes.razorpay_webhook(_signed_request(_sub_event("evt_cancel", "subscription.cancelled")))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "cancelled"
    assert sub.get("cancelled_at")


@pytest.mark.asyncio
async def test_webhook_subscription_completed(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    await routes.razorpay_webhook(_signed_request(_sub_event("evt_done", "subscription.completed")))
    sub = repository.get_pricing_repository().get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "completed"


@pytest.mark.asyncio
async def test_webhook_refund_processed_decrements_pack_credits(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    # Capture first to credit balance.
    sig = _hmac_sha256(f"{created['order_id']}|pay_OK", TEST_KEY_SECRET)
    await routes.verify_order(
        routes.PaymentVerifyRequest(
            payment_id=created["payment_id"],
            razorpay_payment_id="pay_OK",
            razorpay_order_id=created["order_id"],
            razorpay_signature=sig,
        ),
        stub_user,
    )
    repo = repository.get_pricing_repository()
    assert repo.get_balances(user_sub=stub_user["sub"])["zettel"] == 10

    refund_event = {
        "id": "evt_refund_proc_1",
        "event": "refund.processed",
        "payload": {
            "refund": {"entity": {"id": "rfnd_001", "amount": 9900, "speed_processed": "normal"}},
            "payment": {
                "entity": {
                    "id": "pay_OK",
                    "notes": {"payment_id": created["payment_id"], "render_user_id": stub_user["sub"]},
                }
            },
        },
    }
    await routes.razorpay_webhook(_signed_request(refund_event))
    assert repo.get_balances(user_sub=stub_user["sub"])["zettel"] == 0


@pytest.mark.asyncio
async def test_webhook_refund_created_logs_only(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    refund_event = {
        "id": "evt_refund_created_1",
        "event": "refund.created",
        "payload": {
            "refund": {"entity": {"id": "rfnd_002", "amount": 9900}},
            "payment": {
                "entity": {
                    "id": "pay_X",
                    "notes": {"payment_id": created["payment_id"], "render_user_id": stub_user["sub"]},
                }
            },
        },
    }
    response = await routes.razorpay_webhook(_signed_request(refund_event))
    assert response["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_dispute_created_freezes_user(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    dispute_event = {
        "id": "evt_dispute_001",
        "event": "payment.dispute.created",
        "payload": {
            "payment.dispute": {"entity": {"id": "disp_001", "amount": 9900, "reason_code": "fraud"}},
            "payment": {
                "entity": {
                    "id": "pay_X",
                    "notes": {"payment_id": created["payment_id"], "render_user_id": stub_user["sub"]},
                }
            },
        },
    }
    await routes.razorpay_webhook(_signed_request(dispute_event))
    repo = repository.get_pricing_repository()
    assert repo.is_user_dispute_frozen(user_sub=stub_user["sub"])
    # Subsequent order creation should be blocked.
    with pytest.raises(routes.HTTPException) as exc:
        await routes.create_order(
            routes.PaymentCreateRequest(product_id="zettel_10"),
            stub_user,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "account_frozen"


@pytest.mark.asyncio
async def test_webhook_dispute_won_unfreezes_user(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    base_payload = lambda phase, event_id: {  # noqa: E731
        "id": event_id,
        "event": f"payment.dispute.{phase}",
        "payload": {
            "payment.dispute": {"entity": {"id": "disp_002", "amount": 9900}},
            "payment": {
                "entity": {
                    "id": "pay_X",
                    "notes": {"payment_id": created["payment_id"], "render_user_id": stub_user["sub"]},
                }
            },
        },
    }
    await routes.razorpay_webhook(_signed_request(base_payload("created", "evt_disp_c")))
    repo = repository.get_pricing_repository()
    assert repo.is_user_dispute_frozen(user_sub=stub_user["sub"])
    await routes.razorpay_webhook(_signed_request(base_payload("won", "evt_disp_w")))
    assert not repo.is_user_dispute_frozen(user_sub=stub_user["sub"])


@pytest.mark.asyncio
async def test_webhook_dispute_lost_decrements_credits(stub_user, saved_profile, fake_client):
    created = await routes.create_order(
        routes.PaymentCreateRequest(product_id="zettel_10", expected_amount=9900),
        stub_user,
    )
    sig = _hmac_sha256(f"{created['order_id']}|pay_DUP", TEST_KEY_SECRET)
    await routes.verify_order(
        routes.PaymentVerifyRequest(
            payment_id=created["payment_id"],
            razorpay_payment_id="pay_DUP",
            razorpay_order_id=created["order_id"],
            razorpay_signature=sig,
        ),
        stub_user,
    )
    repo = repository.get_pricing_repository()
    assert repo.get_balances(user_sub=stub_user["sub"])["zettel"] == 10

    lost_event = {
        "id": "evt_disp_lost",
        "event": "payment.dispute.lost",
        "payload": {
            "payment.dispute": {"entity": {"id": "disp_003", "amount": 9900}},
            "payment": {
                "entity": {
                    "id": "pay_DUP",
                    "notes": {"payment_id": created["payment_id"], "render_user_id": stub_user["sub"]},
                }
            },
        },
    }
    await routes.razorpay_webhook(_signed_request(lost_event))
    assert repo.get_balances(user_sub=stub_user["sub"])["zettel"] == 0


@pytest.mark.asyncio
async def test_webhook_invoice_expired_marks_grace(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    repo = repository.get_pricing_repository()
    sub = repo.get_subscription(user_sub=stub_user["sub"])
    repo.update_subscription_status(razorpay_subscription_id=sub["razorpay_subscription_id"], status="active")
    event = {
        "id": "evt_inv_exp_1",
        "event": "invoice.expired",
        "payload": {
            "invoice": {"entity": {"id": "inv_001", "subscription_id": "sub_FAKE_1", "notes": {}}}
        },
    }
    await routes.razorpay_webhook(_signed_request(event))
    sub = repo.get_subscription(user_sub=stub_user["sub"])
    assert sub["status"] == "grace"
    assert sub["failure_reason"] == "invoice_expired"


@pytest.mark.asyncio
async def test_webhook_invoice_paid_records_event_only(fake_client, stub_user, saved_profile):
    await routes.create_subscription(
        routes.PaymentCreateRequest(product_id="basic_monthly", expected_amount=14900),
        stub_user,
    )
    event = {
        "id": "evt_inv_paid_1",
        "event": "invoice.paid",
        "payload": {"invoice": {"entity": {"id": "inv_002", "notes": {}}}},
    }
    response = await routes.razorpay_webhook(_signed_request(event))
    assert response["status"] == "ok"


# ───────────────────────── razorpay_client helpers ─────────────────────────


def test_verify_payment_signature_match():
    sig = _hmac_sha256("order_X|pay_X", TEST_KEY_SECRET)
    assert razorpay_client.verify_payment_signature(
        order_id="order_X", payment_id="pay_X", signature=sig
    )


def test_verify_payment_signature_mismatch():
    assert not razorpay_client.verify_payment_signature(
        order_id="order_X", payment_id="pay_X", signature="not-a-real-sig"
    )


def test_verify_webhook_signature_match():
    body = b'{"id":"evt","event":"payment.captured"}'
    sig = hmac.new(TEST_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert razorpay_client.verify_webhook_signature(body=body, signature=sig)


def test_verify_webhook_signature_rejects_empty_secret(monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    body = b'{"x":1}'
    assert not razorpay_client.verify_webhook_signature(body=body, signature="anything")
