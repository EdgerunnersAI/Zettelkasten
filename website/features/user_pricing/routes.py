"""FastAPI routes for pricing catalog, billing profiles, and Razorpay payments.

Architecture:

Packs (Zettel / Kasten / RAG question top-ups)
    POST /api/payments/orders               → Razorpay Order  → Standard Checkout
    POST /api/payments/orders/verify        → HMAC verify + credit balance

Subscriptions (Basic / Max × monthly / quarterly / yearly)
    POST /api/payments/subscriptions        → Razorpay Plan + Subscription
                                              → Standard Checkout (subscription mode)
                                              → user authenticates UPI Autopay / card mandate
                                              → Razorpay charges first cycle, sends events
    POST /api/payments/subscriptions/cancel → Razorpay subscription.cancel + local mark
    GET  /api/payments/subscriptions/me     → Current sub state for the UI

Webhook (canonical truth)
    POST /api/payments/webhook              → signature-verified, idempotent dispatcher
                                              for the full event catalog (payments,
                                              subscriptions, refunds, disputes, invoices)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from website.api.auth import get_current_user
from website.features.user_pricing.catalog import find_product, get_public_catalog
from website.features.user_pricing.razorpay_client import (
    PERIOD_INTERVAL_MAP,
    get_or_create_plan,
    get_razorpay_client,
    get_razorpay_key_id,
    is_razorpay_configured,
    total_count_for,
    verify_payment_signature,
    verify_subscription_signature,
    verify_webhook_signature,
)
from website.features.user_pricing.repository import get_pricing_repository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["user-pricing"])


# ─────────────────────────── request models ───────────────────────────


class BillingProfileRequest(BaseModel):
    phone: str
    name: str = ""

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        phone = "".join(ch for ch in value.strip() if ch.isdigit() or ch == "+")
        if len(phone.replace("+", "")) < 10:
            raise ValueError("Phone number must include at least 10 digits")
        return phone


class PaymentCreateRequest(BaseModel):
    product_id: str
    source: str = "pricing"
    resume_token: str | None = None
    expected_amount: int | None = Field(default=None, ge=0)


class PaymentVerifyRequest(BaseModel):
    payment_id: str
    razorpay_payment_id: str
    razorpay_order_id: str | None = None
    razorpay_subscription_id: str | None = None
    razorpay_signature: str


class SubscriptionCancelRequest(BaseModel):
    cancel_at_cycle_end: bool = False


class SubscriptionChangeRequest(BaseModel):
    to_product_id: str
    expected_amount: int | None = Field(default=None, ge=0)


# ───────────────────────── public catalog routes ─────────────────────────


@router.get("/api/pricing/catalog")
async def catalog() -> dict:
    return get_public_catalog()


@router.get("/api/pricing/billing-profile")
async def billing_profile(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    profile = get_pricing_repository().get_billing_profile(user_sub=user["sub"])
    return {"profile": profile}


@router.put("/api/pricing/billing-profile")
async def update_billing_profile(
    body: BillingProfileRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    metadata = user.get("user_metadata", {})
    return get_pricing_repository().upsert_billing_profile(
        user_sub=user["sub"],
        email=user.get("email", ""),
        phone=body.phone,
        name=body.name or metadata.get("full_name", ""),
    )


# ───────────────────────────── orders (packs) ─────────────────────────────


@router.post("/api/payments/orders")
async def create_order(
    body: PaymentCreateRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    product = find_product(body.product_id)
    if not product or product["kind"] != "pack":
        raise HTTPException(status_code=400, detail={"code": "invalid_product", "message": "Choose a valid pack."})
    _validate_expected_amount(body, product)

    repo = get_pricing_repository()
    if repo.is_user_dispute_frozen(user_sub=user["sub"]):
        raise HTTPException(status_code=409, detail={"code": "account_frozen", "message": "Your account has an open dispute. Contact support."})

    profile = repo.get_billing_profile(user_sub=user["sub"])
    if not profile or not profile.get("phone"):
        raise HTTPException(status_code=400, detail={"code": "billing_profile_required", "message": "Add your phone number before checkout."})

    if not is_razorpay_configured():
        _raise_payments_unavailable()

    amount = int(product["amount"])
    if amount < 100:
        raise HTTPException(status_code=400, detail={"code": "amount_too_low", "message": "Minimum payable amount is ₹1."})

    payment = repo.create_payment_record(
        user_sub=user["sub"],
        product_id=product["id"],
        kind="pack",
        amount=amount,
        currency="INR",
        meter=product.get("meter"),
        quantity=int(product.get("quantity") or 0) or None,
    )

    try:
        client = get_razorpay_client()
        rzp_order = client.order.create(
            data={
                "amount": amount,
                "currency": "INR",
                "receipt": payment["payment_id"][:40],
                "notes": _order_notes(payment, user, product, kind="pack"),
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Razorpay order.create failed for product=%s: %s", product["id"], exc)
        repo.mark_payment_failed(payment_id=payment["payment_id"], reason=str(exc))
        raise HTTPException(status_code=502, detail={"code": "payments_provider_error", "message": "Payment provider unavailable."}) from exc

    repo.attach_provider_order(payment_id=payment["payment_id"], razorpay_order_id=rzp_order["id"])

    return _checkout_payload(
        payment_id=payment["payment_id"],
        amount=amount,
        kind="pack",
        product=product,
        user=user,
        profile=profile,
        order_id=rzp_order["id"],
    )


# ──────────────────────── subscriptions (plans) ────────────────────────


@router.post("/api/payments/subscriptions")
async def create_subscription(
    body: PaymentCreateRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Create a true Razorpay Subscription (Plan + Subscription + mandate).

    Razorpay's Standard Checkout opens with `subscription_id` instead of
    `order_id` — same internal modal popup, same payment methods, but the
    user authenticates a recurring mandate (UPI Autopay or card mandate)
    once. Razorpay then auto-charges every cycle for `total_count` cycles
    and emits `subscription.charged` / `.activated` / `.halted` / etc.
    webhook events that this router fully handles.
    """
    product = find_product(body.product_id)
    if not product or product["kind"] != "subscription":
        raise HTTPException(status_code=400, detail={"code": "invalid_product", "message": "Choose a valid subscription."})
    if product.get("plan_id") == "free":
        raise HTTPException(status_code=400, detail={"code": "free_not_purchasable", "message": "Free tier needs no purchase."})
    _validate_expected_amount(body, product)

    repo = get_pricing_repository()
    if repo.is_user_dispute_frozen(user_sub=user["sub"]):
        raise HTTPException(status_code=409, detail={"code": "account_frozen", "message": "Your account has an open dispute. Contact support."})

    existing = repo.get_subscription(user_sub=user["sub"])
    if existing and existing.get("status") in {"active", "authenticated", "pending", "paused"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "subscription_already_active",
                "message": "You already have an active subscription. Cancel the current one before subscribing to a new plan.",
                "current": _public_subscription(existing),
            },
        )

    profile = repo.get_billing_profile(user_sub=user["sub"])
    if not profile or not profile.get("phone"):
        raise HTTPException(status_code=400, detail={"code": "billing_profile_required", "message": "Add your phone number before checkout."})

    if not is_razorpay_configured():
        _raise_payments_unavailable()

    amount = int(product["amount"])
    if amount < 100:
        raise HTTPException(status_code=400, detail={"code": "amount_too_low", "message": "Minimum payable amount is ₹1."})

    period_label = _detect_period_label(product["id"])
    if period_label not in PERIOD_INTERVAL_MAP:
        raise HTTPException(status_code=400, detail={"code": "unsupported_period", "message": "Unsupported billing period."})

    try:
        razorpay_plan_id = get_or_create_plan(
            period_id=product["id"],
            amount=amount,
            plan_name=f"{product.get('plan_id', '').title()} — {product.get('label', period_label.title())}",
            plan_description=product.get("name") or product["id"],
            period_label=period_label,
        )
    except Exception as exc:
        logger.exception("Razorpay plan create/lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail={"code": "payments_provider_error", "message": "Payment provider unavailable."}) from exc

    # Internal payment record covers the FIRST charge of the subscription.
    # Subsequent renewals get their own records via the webhook handler.
    payment = repo.create_payment_record(
        user_sub=user["sub"],
        product_id=product["id"],
        kind="subscription",
        amount=amount,
        currency="INR",
        plan_id=product.get("plan_id"),
        period_id=product["id"],
    )

    total_count = total_count_for(period_label)

    try:
        client = get_razorpay_client()
        rzp_sub = client.subscription.create(
            data={
                "plan_id": razorpay_plan_id,
                "customer_notify": 1,
                "quantity": 1,
                "total_count": total_count,
                "notes": _subscription_notes(payment, user, product),
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Razorpay subscription.create failed for product=%s: %s", product["id"], exc)
        repo.mark_payment_failed(payment_id=payment["payment_id"], reason=str(exc))
        raise HTTPException(status_code=502, detail={"code": "payments_provider_error", "message": "Payment provider unavailable."}) from exc

    repo.attach_provider_order(payment_id=payment["payment_id"], razorpay_subscription_id=rzp_sub["id"])
    repo.create_or_update_subscription(
        user_sub=user["sub"],
        plan_id=product.get("plan_id") or "",
        period_id=product["id"],
        razorpay_subscription_id=rzp_sub["id"],
        status="created",
        total_count=total_count,
    )

    return _checkout_payload(
        payment_id=payment["payment_id"],
        amount=amount,
        kind="subscription",
        product=product,
        user=user,
        profile=profile,
        subscription_id=rzp_sub["id"],
    )


@router.post("/api/payments/subscriptions/cancel")
async def cancel_subscription(
    body: SubscriptionCancelRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Cancel the user's active Razorpay subscription.

    `cancel_at_cycle_end=False` (default) cancels immediately; the
    `subscription.cancelled` webhook arrives within seconds and finalises
    the local state. `cancel_at_cycle_end=True` lets the user keep paid
    access until the current period ends.
    """
    repo = get_pricing_repository()
    sub = repo.get_subscription(user_sub=user["sub"])
    if not sub or not sub.get("razorpay_subscription_id"):
        raise HTTPException(status_code=404, detail={"code": "no_active_subscription"})

    if sub.get("status") in {"cancelled", "completed"}:
        return {"status": sub["status"], "subscription": _public_subscription(sub)}

    if not is_razorpay_configured():
        _raise_payments_unavailable()

    try:
        client = get_razorpay_client()
        client.subscription.cancel(
            sub["razorpay_subscription_id"],
            {"cancel_at_cycle_end": 1 if body.cancel_at_cycle_end else 0},
        )
    except Exception as exc:
        logger.exception("Razorpay subscription.cancel failed for %s: %s", sub["razorpay_subscription_id"], exc)
        raise HTTPException(status_code=502, detail={"code": "payments_provider_error", "message": "Could not cancel subscription. Try again."}) from exc

    new_status = "pending_cancel" if body.cancel_at_cycle_end else "cancelled"
    updated = repo.update_subscription_status(
        razorpay_subscription_id=sub["razorpay_subscription_id"],
        status=new_status,
        cancelled_at=_now_iso(),
    )
    return {"status": new_status, "subscription": _public_subscription(updated or sub)}


@router.post("/api/payments/subscriptions/change")
async def change_subscription(
    body: SubscriptionChangeRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Cancel the user's current subscription (if any) and create a new one
    for ``to_product_id``. Composes cancel + create from the user's
    perspective: they authenticate one new mandate in the Razorpay modal
    that opens after this call returns.

    Razorpay does not support changing a UPI Autopay mandate's debit amount
    mid-subscription, so an upgrade/downgrade is mechanically a
    cancel-and-recreate. Webhooks ``subscription.cancelled`` (old) and
    ``subscription.activated`` (new) fire in sequence — both keyed by
    ``razorpay_subscription_id``, so order of arrival is safe.

    On partial failure (cancel succeeds, create fails) the old sub is
    cancelled and the user is left without an active sub — they can retry
    from /pricing; the next ``/change`` call will see no active sub and
    fall through to a plain create.
    """
    product = find_product(body.to_product_id)
    if not product or product["kind"] != "subscription":
        raise HTTPException(status_code=400, detail={"code": "invalid_product", "message": "Choose a valid subscription."})
    if product.get("plan_id") == "free":
        raise HTTPException(status_code=400, detail={"code": "free_not_purchasable", "message": "Free tier needs no purchase."})
    if body.expected_amount is not None and int(body.expected_amount) != int(product["amount"]):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "price_changed",
                "message": "The displayed price changed. Refresh pricing before checkout.",
                "expected_amount": body.expected_amount,
                "actual_amount": product["amount"],
                "product_id": body.to_product_id,
            },
        )

    repo = get_pricing_repository()
    if repo.is_user_dispute_frozen(user_sub=user["sub"]):
        raise HTTPException(status_code=409, detail={"code": "account_frozen", "message": "Your account has an open dispute. Contact support."})

    existing = repo.get_subscription(user_sub=user["sub"])
    active_states = {"active", "authenticated", "pending", "paused", "grace"}
    is_active = bool(
        existing
        and existing.get("status") in active_states
        and existing.get("razorpay_subscription_id")
    )

    if is_active and existing.get("period_id") == product["id"]:
        raise HTTPException(
            status_code=409,
            detail={"code": "already_on_this_plan", "message": "You're already on this plan."},
        )

    if is_active:
        if not is_razorpay_configured():
            _raise_payments_unavailable()
        try:
            client = get_razorpay_client()
            client.subscription.cancel(
                existing["razorpay_subscription_id"],
                {"cancel_at_cycle_end": 0},
            )
        except Exception as exc:
            logger.exception(
                "Razorpay cancel during plan change failed for %s: %s",
                existing.get("razorpay_subscription_id"),
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail={"code": "payments_provider_error", "message": "Could not change plan. Try again."},
            ) from exc
        repo.update_subscription_status(
            razorpay_subscription_id=existing["razorpay_subscription_id"],
            status="cancelled",
            cancelled_at=_now_iso(),
        )

    return await create_subscription(
        PaymentCreateRequest(
            product_id=body.to_product_id,
            source="change",
            expected_amount=body.expected_amount,
        ),
        user,
    )


@router.get("/api/payments/subscriptions/me")
async def my_subscription(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    sub = get_pricing_repository().get_subscription(user_sub=user["sub"])
    if not sub:
        return {"subscription": None}
    return {"subscription": _public_subscription(sub)}


# ─────────────────────────── payment verify ───────────────────────────


@router.post("/api/payments/orders/verify")
async def verify_order(
    body: PaymentVerifyRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    repo = get_pricing_repository()
    record = repo.get_payment_record(payment_id=body.payment_id)
    if not record or record.get("render_user_id") != user["sub"]:
        raise HTTPException(status_code=404, detail={"code": "payment_not_found"})

    is_subscription = bool(body.razorpay_subscription_id) and not body.razorpay_order_id
    if is_subscription:
        ok = verify_subscription_signature(
            payment_id=body.razorpay_payment_id,
            subscription_id=body.razorpay_subscription_id or "",
            signature=body.razorpay_signature,
        )
    else:
        order_id = body.razorpay_order_id or record.get("razorpay_order_id") or ""
        if not order_id:
            raise HTTPException(status_code=400, detail={"code": "missing_order_id"})
        ok = verify_payment_signature(
            order_id=order_id,
            payment_id=body.razorpay_payment_id,
            signature=body.razorpay_signature,
        )

    if not ok:
        repo.mark_payment_failed(payment_id=body.payment_id, reason="signature_mismatch")
        raise HTTPException(status_code=400, detail={"code": "signature_mismatch", "message": "Payment verification failed."})

    # Replay-safety: a client that re-submits a valid verify (network retry,
    # browser back-button, double-tap) must NOT double-credit packs or
    # re-activate a subscription. The webhook layer already gates duplicates
    # by event_id; this guards the client-callable path with the same intent.
    if record.get("status") == "paid":
        return {"status": "paid", "payment": _public_payment(record)}

    updated = repo.mark_payment_paid(
        payment_id=body.payment_id,
        razorpay_payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
    )
    _apply_fulfillment(record=updated)

    return {"status": "paid", "payment": _public_payment(updated)}


# ─────────────────────────── webhook dispatcher ───────────────────────────


@router.post("/api/payments/webhook")
async def razorpay_webhook(request: Request) -> dict:
    """Source of truth for Razorpay event state transitions.

    Signature-verified, idempotent (`event.id` dedup), and dispatches to
    explicit handlers per event type. Events with handlers below cover
    the full Razorpay catalog relevant to packs + subscriptions: payment
    lifecycle, order completion, refunds, disputes, subscription lifecycle,
    and invoice state.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_webhook_signature(body=raw_body, signature=signature):
        raise HTTPException(status_code=400, detail={"code": "invalid_signature"})

    try:
        event = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail={"code": "invalid_json"})

    event_id = event.get("id") or event.get("event_id") or ""
    event_type = event.get("event") or ""
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail={"code": "missing_event_fields"})

    repo = get_pricing_repository()
    if repo.event_already_processed(event_id=event_id):
        return {"status": "duplicate", "event_id": event_id}

    handler = _WEBHOOK_HANDLERS.get(event_type)
    if handler is None:
        # Unknown event type — log + record (idempotency) but don't error.
        # Razorpay may add new events; we want to be permissive on receipt
        # while explicit on what we act upon.
        logger.info("Razorpay event with no handler: %s id=%s", event_type, event_id)
        repo.record_event(event_id=event_id, event_type=event_type, payment_id=None, payload=event)
        return {"status": "ignored", "event": event_type, "event_id": event_id}

    payload = event.get("payload") or {}
    payment_id_internal: str | None = None
    try:
        payment_id_internal = handler(repo, event, payload)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("Razorpay webhook handler %s raised: %s", event_type, exc)
        # Fall through to record + return 200 so Razorpay does not retry-storm.
        # Real failures should surface in logs + ops alerting.

    repo.record_event(event_id=event_id, event_type=event_type, payment_id=payment_id_internal, payload=event)
    return {"status": "ok", "event_id": event_id, "event": event_type}


# ───────────────── per-event handlers (return internal payment_id) ─────────────────


def _h_payment_authorized(repo, event, payload) -> str | None:
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    notes = payment_entity.get("notes") or {}
    pid = notes.get("payment_id")
    if pid:
        rec = repo.get_payment_record(payment_id=pid) or {}
        if rec.get("status") not in {"paid", "refunded"}:
            repo.attach_provider_order(payment_id=pid, razorpay_order_id=payment_entity.get("order_id"))
    return pid


def _h_payment_captured(repo, event, payload) -> str | None:
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    order_entity = (payload.get("order") or {}).get("entity") or {}
    notes = payment_entity.get("notes") or order_entity.get("notes") or {}
    pid = notes.get("payment_id")
    razorpay_payment_id = payment_entity.get("id") or ""
    if pid and razorpay_payment_id:
        updated = repo.mark_payment_paid(payment_id=pid, razorpay_payment_id=razorpay_payment_id)
        _apply_fulfillment(record=updated)
    return pid


def _h_order_paid(repo, event, payload) -> str | None:
    # Same fulfillment as payment.captured — Razorpay sends both for orders.
    # Idempotency on payment_id status prevents double-credit.
    return _h_payment_captured(repo, event, payload)


def _h_payment_failed(repo, event, payload) -> str | None:
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    notes = payment_entity.get("notes") or {}
    pid = notes.get("payment_id")
    if pid:
        repo.mark_payment_failed(
            payment_id=pid,
            reason=payment_entity.get("error_description") or "payment_failed",
        )
    return pid


def _h_subscription_authenticated(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    repo.update_subscription_status(
        razorpay_subscription_id=sub_entity.get("id") or "",
        status="authenticated",
    )
    notes = sub_entity.get("notes") or {}
    return notes.get("payment_id")


def _h_subscription_activated(repo, event, payload) -> str | None:
    # WAVE-A P1: owner MUST be derived from DB lookup keyed by razorpay
    # subscription id — never from notes.render_user_id (caller-controlled).
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    notes = sub_entity.get("notes") or {}
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    pid = notes.get("payment_id")
    months = int(notes.get("months") or 1)
    plan_id = notes.get("plan_id") or ""
    period_id = notes.get("period_id") or ""
    razorpay_sub_id = sub_entity.get("id") or ""
    existing = (
        repo.get_subscription_by_razorpay_id(razorpay_subscription_id=razorpay_sub_id)
        if razorpay_sub_id
        else None
    )
    user_sub = (existing or {}).get("render_user_id") if existing else None
    if user_sub and plan_id:
        repo.activate_subscription(
            user_sub=user_sub,
            plan_id=plan_id,
            period_id=period_id,
            months=months,
            razorpay_subscription_id=razorpay_sub_id,
            razorpay_payment_id=payment_entity.get("id"),
        )
    elif razorpay_sub_id:
        # No DB row → cannot attribute. Log + return 200 so Razorpay does not retry.
        logger.warning(
            "subscription.activated: no DB row for razorpay_subscription_id=%s; ignoring (refusing to trust notes.render_user_id)",
            razorpay_sub_id,
        )
    if pid and payment_entity.get("id"):
        updated = repo.mark_payment_paid(payment_id=pid, razorpay_payment_id=payment_entity["id"])
        _apply_fulfillment(record=updated)
    return pid


def _h_subscription_charged(repo, event, payload) -> str | None:
    # Recurring renewal succeeded — extend period_end and bump paid_count.
    # WAVE-A P1: owner via DB lookup, not notes.render_user_id.
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    notes = sub_entity.get("notes") or {}
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    months = int(notes.get("months") or 1)
    plan_id = notes.get("plan_id") or ""
    period_id = notes.get("period_id") or ""
    razorpay_sub_id = sub_entity.get("id") or ""
    existing = (
        repo.get_subscription_by_razorpay_id(razorpay_subscription_id=razorpay_sub_id)
        if razorpay_sub_id
        else None
    )
    user_sub = (existing or {}).get("render_user_id") if existing else None
    if user_sub and plan_id:
        repo.activate_subscription(
            user_sub=user_sub,
            plan_id=plan_id,
            period_id=period_id,
            months=months,
            razorpay_subscription_id=razorpay_sub_id,
            razorpay_payment_id=payment_entity.get("id"),
        )
    elif razorpay_sub_id:
        logger.warning(
            "subscription.charged: no DB row for razorpay_subscription_id=%s; ignoring (refusing to trust notes.render_user_id)",
            razorpay_sub_id,
        )
    return notes.get("payment_id")


def _h_subscription_pending(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    repo.update_subscription_status(
        razorpay_subscription_id=sub_entity.get("id") or "",
        status="grace",
        failure_reason="payment_retry",
    )
    return None


def _h_subscription_halted(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    repo.update_subscription_status(
        razorpay_subscription_id=sub_entity.get("id") or "",
        status="halted",
        failure_reason="retries_exhausted",
    )
    return None


def _h_subscription_paused(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    repo.update_subscription_status(
        razorpay_subscription_id=sub_entity.get("id") or "",
        status="paused",
    )
    return None


def _h_subscription_resumed(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    repo.update_subscription_status(
        razorpay_subscription_id=sub_entity.get("id") or "",
        status="active",
    )
    return None


def _h_subscription_cancelled(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    repo.update_subscription_status(
        razorpay_subscription_id=sub_entity.get("id") or "",
        status="cancelled",
        cancelled_at=_now_iso(),
    )
    return None


def _h_subscription_completed(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    repo.update_subscription_status(
        razorpay_subscription_id=sub_entity.get("id") or "",
        status="completed",
    )
    return None


def _h_subscription_updated(repo, event, payload) -> str | None:
    sub_entity = (payload.get("subscription") or {}).get("entity") or {}
    # Plan / quantity changes — keep status as-is, sync sub fields.
    rid = sub_entity.get("id") or ""
    existing = repo.get_subscription_by_razorpay_id(razorpay_subscription_id=rid)
    if existing:
        repo.update_subscription_status(razorpay_subscription_id=rid, status=existing.get("status") or "active")
    return None


def _h_refund_created(repo, event, payload) -> str | None:
    refund_entity = (payload.get("refund") or {}).get("entity") or {}
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    notes = payment_entity.get("notes") or {}
    pid = notes.get("payment_id")
    repo.record_refund(
        razorpay_refund_id=refund_entity.get("id") or "",
        razorpay_payment_id=payment_entity.get("id") or refund_entity.get("payment_id"),
        payment_id=pid,
        render_user_id=notes.get("render_user_id"),
        amount=int(refund_entity.get("amount") or 0),
        status="created",
        speed=refund_entity.get("speed_processed"),
        notes=notes,
    )
    return pid


def _h_refund_processed(repo, event, payload) -> str | None:
    refund_entity = (payload.get("refund") or {}).get("entity") or {}
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    notes = payment_entity.get("notes") or {}
    pid = notes.get("payment_id")
    refund_amount = int(refund_entity.get("amount") or 0)
    record = repo.get_payment_record(payment_id=pid) if pid else None
    # BOLA guard: bind record to the Razorpay-issued payment id from the signed
    # envelope. notes.payment_id is client-controllable; an attacker holding the
    # webhook secret could otherwise pivot a refund onto a victim's record.
    rzp_pay_id = payment_entity.get("id") or refund_entity.get("payment_id")
    if record and rzp_pay_id and record.get("razorpay_payment_id") and record.get("razorpay_payment_id") != rzp_pay_id:
        logger.warning(
            "Refund event id mismatch: notes.payment_id=%s but Razorpay payment_id=%s; rejecting",
            pid,
            rzp_pay_id,
        )
        return None
    if record:
        # Decrement pack credits proportionally to the refund amount.
        if record.get("kind") == "pack" and record.get("meter") and record.get("quantity"):
            full_amount = int(record.get("amount") or 0)
            if full_amount > 0:
                refund_qty = int(record["quantity"]) * refund_amount // full_amount
                if refund_qty > 0:
                    repo.deduct_pack_credits(
                        user_sub=record["render_user_id"], meter=record["meter"], quantity=refund_qty
                    )
        record_updated = dict(record)
        record_updated["status"] = "refunded"
        repo.mark_payment_failed(payment_id=record["payment_id"], reason="refunded")  # marks status; reason is descriptive
    repo.record_refund(
        razorpay_refund_id=refund_entity.get("id") or "",
        razorpay_payment_id=payment_entity.get("id") or refund_entity.get("payment_id"),
        payment_id=pid,
        render_user_id=notes.get("render_user_id"),
        amount=refund_amount,
        status="processed",
        speed=refund_entity.get("speed_processed"),
        notes=notes,
    )
    return pid


def _h_refund_failed(repo, event, payload) -> str | None:
    refund_entity = (payload.get("refund") or {}).get("entity") or {}
    payment_entity = (payload.get("payment") or {}).get("entity") or {}
    notes = payment_entity.get("notes") or {}
    repo.record_refund(
        razorpay_refund_id=refund_entity.get("id") or "",
        razorpay_payment_id=payment_entity.get("id") or refund_entity.get("payment_id"),
        payment_id=notes.get("payment_id"),
        render_user_id=notes.get("render_user_id"),
        amount=int(refund_entity.get("amount") or 0),
        status="failed",
        notes=notes,
    )
    return notes.get("payment_id")


def _dispute_handler(phase: str) -> Callable:
    def _h(repo, event, payload) -> str | None:
        dispute_entity = (payload.get("payment.dispute") or payload.get("dispute") or {}).get("entity") or {}
        payment_entity = (payload.get("payment") or {}).get("entity") or {}
        notes = payment_entity.get("notes") or {}
        pid = notes.get("payment_id")

        # BOLA guard (same class as refund.processed): if notes.payment_id
        # resolves to a record whose stored razorpay_payment_id does NOT match
        # the Razorpay-issued id in the signed envelope, refuse to act on it.
        # We must short-circuit BEFORE record_dispute so a spoofed render_user_id
        # cannot freeze the victim's account via _DISPUTE_FROZEN.
        rzp_pay_id = payment_entity.get("id") or dispute_entity.get("payment_id")
        record = repo.get_payment_record(payment_id=pid) if pid else None
        if record and rzp_pay_id and record.get("razorpay_payment_id") and record.get("razorpay_payment_id") != rzp_pay_id:
            logger.warning(
                "Dispute %s event id mismatch: notes.payment_id=%s but Razorpay payment_id=%s; rejecting",
                phase,
                pid,
                rzp_pay_id,
            )
            return None

        repo.record_dispute(
            razorpay_dispute_id=dispute_entity.get("id") or "",
            razorpay_payment_id=payment_entity.get("id") or dispute_entity.get("payment_id"),
            payment_id=pid,
            render_user_id=notes.get("render_user_id"),
            amount=int(dispute_entity.get("amount") or 0),
            phase=phase,
            reason_code=dispute_entity.get("reason_code"),
            payload=event,
        )
        # On 'lost', deduct pack credits same as refund processed.
        if phase == "lost":
            if record and record.get("kind") == "pack" and record.get("meter") and record.get("quantity"):
                repo.deduct_pack_credits(
                    user_sub=record["render_user_id"], meter=record["meter"], quantity=int(record["quantity"])
                )
        return pid

    return _h


def _h_invoice_paid(repo, event, payload) -> str | None:
    # Razorpay sends invoice.paid alongside subscription.charged for subs.
    # Subscription.charged already extends period_end; this handler simply
    # records the invoice for audit (idempotent via event_id).
    inv_entity = (payload.get("invoice") or {}).get("entity") or {}
    notes = (inv_entity.get("notes") or {})
    return notes.get("payment_id")


def _h_invoice_partially_paid(repo, event, payload) -> str | None:
    return _h_invoice_paid(repo, event, payload)


def _h_invoice_expired(repo, event, payload) -> str | None:
    inv_entity = (payload.get("invoice") or {}).get("entity") or {}
    sub_id = inv_entity.get("subscription_id")
    if sub_id:
        existing = repo.get_subscription_by_razorpay_id(razorpay_subscription_id=sub_id)
        if existing and existing.get("status") in {"active", "authenticated"}:
            repo.update_subscription_status(
                razorpay_subscription_id=sub_id,
                status="grace",
                failure_reason="invoice_expired",
            )
    return None


_WEBHOOK_HANDLERS: dict[str, Callable] = {
    # Payments
    "payment.authorized": _h_payment_authorized,
    "payment.captured": _h_payment_captured,
    "payment.failed": _h_payment_failed,
    "order.paid": _h_order_paid,

    # Refunds
    "refund.created": _h_refund_created,
    "refund.processed": _h_refund_processed,
    "refund.failed": _h_refund_failed,

    # Disputes
    "payment.dispute.created": _dispute_handler("created"),
    "payment.dispute.under_review": _dispute_handler("under_review"),
    "payment.dispute.action_required": _dispute_handler("action_required"),
    "payment.dispute.won": _dispute_handler("won"),
    "payment.dispute.lost": _dispute_handler("lost"),
    "payment.dispute.closed": _dispute_handler("closed"),

    # Subscriptions
    "subscription.authenticated": _h_subscription_authenticated,
    "subscription.activated": _h_subscription_activated,
    "subscription.charged": _h_subscription_charged,
    "subscription.pending": _h_subscription_pending,
    "subscription.halted": _h_subscription_halted,
    "subscription.paused": _h_subscription_paused,
    "subscription.resumed": _h_subscription_resumed,
    "subscription.cancelled": _h_subscription_cancelled,
    "subscription.completed": _h_subscription_completed,
    "subscription.updated": _h_subscription_updated,

    # Invoices
    "invoice.paid": _h_invoice_paid,
    "invoice.partially_paid": _h_invoice_partially_paid,
    "invoice.expired": _h_invoice_expired,
}


# ─────────────────────────── status / helpers ───────────────────────────


@router.get("/api/payments/status/{payment_id}")
async def payment_status(
    payment_id: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    record = get_pricing_repository().get_payment_record(payment_id=payment_id)
    if not record or record.get("render_user_id") != user["sub"]:
        raise HTTPException(status_code=404, detail={"code": "payment_not_found"})
    return {"payment": _public_payment(record)}


def _checkout_payload(
    *,
    payment_id: str,
    amount: int,
    kind: str,
    product: dict,
    user: dict,
    profile: dict,
    order_id: str | None = None,
    subscription_id: str | None = None,
) -> dict:
    description = product.get("name") or product.get("label") or product["id"]
    base = {
        "payment_id": payment_id,
        "kind": kind,
        "key_id": get_razorpay_key_id(),
        "amount": amount,
        "currency": "INR",
        "name": "Zettelkasten",
        "image": "/artifacts/company_logo.svg",
        "description": str(description),
        "product": {
            "id": product["id"],
            "name": product.get("name") or product.get("label") or product["id"],
            "kind": kind,
            "amount": amount,
        },
        "prefill": {
            "name": profile.get("name") or user.get("user_metadata", {}).get("full_name", ""),
            "email": user.get("email", "") or profile.get("email", ""),
            "contact": profile.get("phone", ""),
        },
        "notes": {"payment_id": payment_id, "product_id": product["id"]},
        "theme": {"color": "#0d9488"},
    }
    if subscription_id:
        base["subscription_id"] = subscription_id
        base["recurring"] = True
    if order_id:
        base["order_id"] = order_id
    return base


def _public_payment(record: dict) -> dict:
    return {
        "payment_id": record.get("payment_id"),
        "status": record.get("status"),
        "kind": record.get("kind"),
        "amount": record.get("amount"),
        "currency": record.get("currency"),
        "product_id": record.get("product_id"),
        "razorpay_payment_id": record.get("razorpay_payment_id"),
        "razorpay_order_id": record.get("razorpay_order_id"),
        "razorpay_subscription_id": record.get("razorpay_subscription_id"),
        "paid_at": record.get("paid_at"),
    }


def _public_subscription(sub: dict) -> dict:
    return {
        "plan_id": sub.get("plan_id"),
        "period_id": sub.get("period_id"),
        "status": sub.get("status"),
        "current_period_start": sub.get("current_period_start"),
        "current_period_end": sub.get("current_period_end"),
        "cancelled_at": sub.get("cancelled_at"),
        "razorpay_subscription_id": sub.get("razorpay_subscription_id"),
    }


def _apply_fulfillment(*, record: dict) -> None:
    """Credit packs to balance / activate subscription after successful payment."""
    repo = get_pricing_repository()
    user_sub = record.get("render_user_id")
    if not user_sub:
        return
    # Pause fulfillment for users with an open dispute.
    if repo.is_user_dispute_frozen(user_sub=user_sub):
        return

    if record.get("kind") == "pack":
        meter = record.get("meter")
        quantity = int(record.get("quantity") or 0)
        if meter and quantity > 0:
            repo.add_pack_credits(user_sub=user_sub, meter=meter, quantity=quantity)
        elif record.get("product_id"):
            product = find_product(record["product_id"])
            if product and product.get("meter") and product.get("quantity"):
                repo.add_pack_credits(user_sub=user_sub, meter=product["meter"], quantity=int(product["quantity"]))
    elif record.get("kind") == "subscription":
        product_id = record.get("product_id") or record.get("period_id")
        product = find_product(product_id) if product_id else None
        if product:
            repo.activate_subscription(
                user_sub=user_sub,
                plan_id=record.get("plan_id") or product.get("plan_id") or "",
                period_id=product["id"],
                months=int(product.get("months") or 1),
                razorpay_subscription_id=record.get("razorpay_subscription_id"),
                razorpay_payment_id=record.get("razorpay_payment_id"),
            )


def _order_notes(payment: dict, user: dict, product: dict, *, kind: str) -> dict:
    return {
        "payment_id": payment["payment_id"],
        "render_user_id": user["sub"],
        "product_id": product["id"],
        "kind": kind,
        "meter": product.get("meter") or "",
        "quantity": str(int(product.get("quantity") or 0)),
    }


def _subscription_notes(payment: dict, user: dict, product: dict) -> dict:
    return {
        "payment_id": payment["payment_id"],
        "render_user_id": user["sub"],
        "product_id": product["id"],
        "kind": "subscription",
        "plan_id": product.get("plan_id") or "",
        "period_id": product["id"],
        "months": str(int(product.get("months") or 1)),
    }


def _detect_period_label(period_id: str) -> str:
    if period_id.endswith("_monthly"):
        return "monthly"
    if period_id.endswith("_quarterly"):
        return "quarterly"
    if period_id.endswith("_yearly"):
        return "yearly"
    return ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _customer_details(user: dict, profile: dict) -> dict[str, str]:
    return {
        "customer_id": user["sub"],
        "customer_email": user.get("email", "") or profile.get("email", ""),
        "customer_phone": profile["phone"],
        "customer_name": profile.get("name", ""),
    }


def _raise_payments_unavailable() -> None:
    raise HTTPException(
        status_code=503,
        detail={"code": "payments_not_configured", "message": "Payments are not configured."},
    )


def _validate_expected_amount(body: PaymentCreateRequest, product: dict) -> None:
    if body.expected_amount is None:
        return
    if int(body.expected_amount) != int(product["amount"]):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "price_changed",
                "message": "The displayed price changed. Refresh pricing before checkout.",
                "expected_amount": body.expected_amount,
                "actual_amount": product["amount"],
                "product_id": body.product_id,
            },
        )
