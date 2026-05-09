"""Persistence facade for pricing state.

The concrete production schema is provided by the Supabase migration
(``supabase/website/user_pricing/schema.sql``). This facade stays small so
route code can be tested without a live database and without leaking
provider details into API handlers.

In-memory dicts are used as a graceful fallback whenever Supabase is not
configured or a write fails — entitlement checks "fail open" rather than
blocking real users on infra hiccups.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from website.core.db_version import use_supabase_v2
from website.core.supabase_v2.client import is_v2_configured as is_supabase_configured
from website.core.supabase_v2.repositories.billing_repository import BillingRepository as V2BillingRepository
from website.features.user_pricing.models import Meter

logger = logging.getLogger(__name__)

_MEMORY_PROFILES: dict[str, dict] = {}
_MEMORY_PAYMENTS: dict[str, dict] = {}
_MEMORY_BALANCES: dict[str, dict[str, int]] = {}
_MEMORY_SUBSCRIPTIONS: dict[str, dict] = {}  # keyed by render_user_id (current sub)
_MEMORY_SUBS_BY_RZP: dict[str, str] = {}      # razorpay_subscription_id -> render_user_id
_MEMORY_EVENTS: dict[str, dict] = {}
_MEMORY_REFUNDS: dict[str, dict] = {}         # razorpay_refund_id -> row
_MEMORY_DISPUTES: dict[str, dict] = {}        # razorpay_dispute_id -> row
_MEMORY_PLAN_CACHE: dict[str, str] = {}       # "{period_id}:{amount}" -> razorpay_plan_id

# Set of users with an open dispute — fulfillment is paused while in this set.
_DISPUTE_FROZEN: set[str] = set()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PricingRepository:
    """Supabase-backed pricing repository with in-memory fallback."""

    def _v2_profile_id(self, user_sub: str) -> UUID | None:
        if not use_supabase_v2():
            return None
        try:
            return UUID(user_sub)
        except (TypeError, ValueError):
            return None

    # ────────────────────────── entitlements ──────────────────────────

    def check_entitlement(self, *, user_sub: str, meter: Meter, action_id: str | None) -> bool:
        v2_profile_id = self._v2_profile_id(user_sub)
        if v2_profile_id is not None:
            try:
                return V2BillingRepository().check_entitlement(
                    profile_id=v2_profile_id,
                    feature=str(meter),
                    unit="request",
                )
            except Exception as exc:
                logger.warning("Pricing v2 entitlement check failed open for user=%s meter=%s: %s", user_sub, meter, exc)
                return True

        if not is_supabase_configured():
            return True

        try:
            from website.core.persist import get_supabase_scope

            scoped = get_supabase_scope(user_id_override=user_sub)
            if not scoped:
                return True
            repo, _kg_user_id = scoped
            response = repo._client.rpc(
                "pricing_check_entitlement",
                {"p_render_user_id": user_sub, "p_meter": str(meter), "p_action_id": action_id},
            ).execute()
            return bool(response.data)
        except Exception as exc:
            logger.warning("Pricing entitlement check failed open for user=%s meter=%s: %s", user_sub, meter, exc)
            return True

    def consume_entitlement(self, *, user_sub: str, meter: Meter, action_id: str | None) -> None:
        v2_profile_id = self._v2_profile_id(user_sub)
        if v2_profile_id is not None:
            try:
                V2BillingRepository().check_entitlement(
                    profile_id=v2_profile_id,
                    feature=str(meter),
                    unit="request",
                )
            except Exception as exc:
                logger.warning("Pricing v2 entitlement consume failed for user=%s meter=%s: %s", user_sub, meter, exc)
            return

        if not is_supabase_configured():
            return

        try:
            from website.core.persist import get_supabase_scope

            scoped = get_supabase_scope(user_id_override=user_sub)
            if not scoped:
                return
            repo, _kg_user_id = scoped
            repo._client.rpc(
                "pricing_consume_entitlement",
                {"p_render_user_id": user_sub, "p_meter": str(meter), "p_action_id": action_id},
            ).execute()
        except Exception as exc:
            logger.warning("Pricing entitlement consume failed for user=%s meter=%s: %s", user_sub, meter, exc)

    # ───────────────────────── billing profile ─────────────────────────

    def get_billing_profile(self, *, user_sub: str) -> dict | None:
        if not is_supabase_configured():
            return _MEMORY_PROFILES.get(user_sub)

        try:
            from website.core.persist import get_supabase_scope

            scoped = get_supabase_scope(user_id_override=user_sub)
            if not scoped:
                return None
            repo, _kg_user_id = scoped
            response = (
                repo._client.table("pricing_billing_profiles")
                .select("*")
                .eq("render_user_id", user_sub)
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.warning("Billing profile lookup failed for user=%s: %s", user_sub, exc)
            return _MEMORY_PROFILES.get(user_sub)

    def upsert_billing_profile(self, *, user_sub: str, email: str, phone: str, name: str = "") -> dict:
        row = {
            "render_user_id": user_sub,
            "email": email,
            "phone": phone,
            "name": name,
            "updated_at": _now_iso(),
        }
        if not is_supabase_configured():
            _MEMORY_PROFILES[user_sub] = row
            return row

        try:
            from website.core.persist import get_supabase_scope

            scoped = get_supabase_scope(user_id_override=user_sub)
            if not scoped:
                _MEMORY_PROFILES[user_sub] = row
                return row
            repo, _kg_user_id = scoped
            response = (
                repo._client.table("pricing_billing_profiles")
                .upsert(row, on_conflict="render_user_id")
                .execute()
            )
            return response.data[0] if response.data else row
        except Exception as exc:
            logger.warning("Billing profile upsert failed for user=%s: %s", user_sub, exc)
            _MEMORY_PROFILES[user_sub] = row
            return row

    # ─────────────────────────── payments ────────────────────────────

    def create_payment_record(
        self,
        *,
        user_sub: str,
        product_id: str,
        kind: str,
        amount: int,
        currency: str,
        plan_id: str | None = None,
        period_id: str | None = None,
        meter: str | None = None,
        quantity: int | None = None,
    ) -> dict:
        payment_id = f"zk_{kind}_{uuid4().hex}"
        row = {
            "payment_id": payment_id,
            "render_user_id": user_sub,
            "product_id": product_id,
            "kind": kind,
            "amount": int(amount),
            "currency": currency,
            "status": "created",
            "plan_id": plan_id,
            "period_id": period_id,
            "meter": meter,
            "quantity": int(quantity) if quantity is not None else None,
            "razorpay_order_id": None,
            "razorpay_subscription_id": None,
            "razorpay_payment_id": None,
            "created_at": _now_iso(),
        }
        _MEMORY_PAYMENTS[payment_id] = row
        if is_supabase_configured():
            self._supabase_insert("pricing_orders", row)
        return row

    def attach_provider_order(
        self,
        *,
        payment_id: str,
        razorpay_order_id: str | None = None,
        razorpay_subscription_id: str | None = None,
    ) -> dict:
        row = _MEMORY_PAYMENTS.setdefault(payment_id, {"payment_id": payment_id})
        if razorpay_order_id is not None:
            row["razorpay_order_id"] = razorpay_order_id
        if razorpay_subscription_id is not None:
            row["razorpay_subscription_id"] = razorpay_subscription_id
        row["updated_at"] = _now_iso()
        if is_supabase_configured():
            self._supabase_update(
                "pricing_orders",
                {
                    k: v
                    for k, v in {
                        "razorpay_order_id": razorpay_order_id,
                        "razorpay_subscription_id": razorpay_subscription_id,
                        "updated_at": row["updated_at"],
                    }.items()
                    if v is not None
                },
                where=("payment_id", payment_id),
            )
        return row

    def mark_payment_paid(
        self,
        *,
        payment_id: str,
        razorpay_payment_id: str,
        signature: str | None = None,
    ) -> dict:
        row = _MEMORY_PAYMENTS.setdefault(payment_id, {"payment_id": payment_id})
        row["razorpay_payment_id"] = razorpay_payment_id
        row["status"] = "paid"
        row["signature"] = signature
        row["paid_at"] = _now_iso()
        row["updated_at"] = row["paid_at"]
        if is_supabase_configured():
            self._supabase_update(
                "pricing_orders",
                {
                    "razorpay_payment_id": razorpay_payment_id,
                    "status": "paid",
                    "paid_at": row["paid_at"],
                    "updated_at": row["paid_at"],
                },
                where=("payment_id", payment_id),
            )
        return row

    def mark_payment_failed(self, *, payment_id: str, reason: str) -> dict:
        row = _MEMORY_PAYMENTS.setdefault(payment_id, {"payment_id": payment_id})
        row["status"] = "failed"
        row["failure_reason"] = reason
        row["updated_at"] = _now_iso()
        if is_supabase_configured():
            self._supabase_update(
                "pricing_orders",
                {"status": "failed", "failure_reason": reason, "updated_at": row["updated_at"]},
                where=("payment_id", payment_id),
            )
        return row

    def get_payment_record(self, *, payment_id: str) -> dict | None:
        if payment_id in _MEMORY_PAYMENTS:
            return _MEMORY_PAYMENTS[payment_id]
        if is_supabase_configured():
            try:
                from website.core.persist import get_supabase_scope

                scoped = get_supabase_scope()
                if not scoped:
                    return None
                repo, _ = scoped
                response = (
                    repo._client.table("pricing_orders")
                    .select("*")
                    .eq("payment_id", payment_id)
                    .limit(1)
                    .execute()
                )
                if response.data:
                    _MEMORY_PAYMENTS[payment_id] = response.data[0]
                    return response.data[0]
            except Exception as exc:
                logger.warning("Payment lookup failed for %s: %s", payment_id, exc)
        return None

    def find_payment_by_razorpay_order(self, *, razorpay_order_id: str) -> dict | None:
        for row in _MEMORY_PAYMENTS.values():
            if row.get("razorpay_order_id") == razorpay_order_id:
                return row
        if is_supabase_configured():
            try:
                from website.core.persist import get_supabase_scope

                scoped = get_supabase_scope()
                if not scoped:
                    return None
                repo, _ = scoped
                response = (
                    repo._client.table("pricing_orders")
                    .select("*")
                    .eq("razorpay_order_id", razorpay_order_id)
                    .limit(1)
                    .execute()
                )
                if response.data:
                    _MEMORY_PAYMENTS[response.data[0]["payment_id"]] = response.data[0]
                    return response.data[0]
            except Exception as exc:
                logger.warning("Payment lookup by order failed for %s: %s", razorpay_order_id, exc)
        return None

    # ─────────────────────────── balances ────────────────────────────

    def add_pack_credits(self, *, user_sub: str, meter: str, quantity: int) -> dict[str, int]:
        wallet = _MEMORY_BALANCES.setdefault(user_sub, {})
        wallet[meter] = int(wallet.get(meter, 0)) + int(quantity)
        if is_supabase_configured():
            try:
                from website.core.persist import get_supabase_scope

                scoped = get_supabase_scope(user_id_override=user_sub)
                if scoped:
                    repo, _ = scoped
                    repo._client.rpc(
                        "pricing_add_pack_credits",
                        {"p_render_user_id": user_sub, "p_meter": meter, "p_quantity": int(quantity)},
                    ).execute()
            except Exception as exc:
                logger.warning("add_pack_credits supabase failed for user=%s meter=%s: %s", user_sub, meter, exc)
        return dict(wallet)

    def get_balances(self, *, user_sub: str) -> dict[str, int]:
        return dict(_MEMORY_BALANCES.get(user_sub, {}))

    # ───────────────────────── subscriptions ─────────────────────────

    def create_or_update_subscription(
        self,
        *,
        user_sub: str,
        plan_id: str,
        period_id: str,
        razorpay_subscription_id: str,
        status: str = "created",
        total_count: int | None = None,
    ) -> dict:
        """Insert or refresh the user's current subscription row.

        Used at /api/payments/subscriptions creation time to record the
        Razorpay subscription before the user authenticates the mandate.
        """
        existing = _MEMORY_SUBSCRIPTIONS.get(user_sub) or {}
        row = {
            **existing,
            "render_user_id": user_sub,
            "plan_id": plan_id,
            "period_id": period_id,
            "status": status,
            "razorpay_subscription_id": razorpay_subscription_id,
            "total_count": total_count if total_count is not None else existing.get("total_count"),
            "current_period_start": existing.get("current_period_start") or _now_iso(),
            "current_period_end": existing.get("current_period_end"),
            "paid_count": existing.get("paid_count", 0),
            "updated_at": _now_iso(),
        }
        _MEMORY_SUBSCRIPTIONS[user_sub] = row
        if razorpay_subscription_id:
            _MEMORY_SUBS_BY_RZP[razorpay_subscription_id] = user_sub
        if is_supabase_configured():
            self._supabase_insert("pricing_subscriptions", row, upsert_on="render_user_id")
        return row

    def activate_subscription(
        self,
        *,
        user_sub: str,
        plan_id: str,
        period_id: str,
        months: int,
        razorpay_subscription_id: str | None = None,
        razorpay_payment_id: str | None = None,
    ) -> dict:
        start = datetime.now(UTC)
        end = start + timedelta(days=int(months) * 30)
        existing = _MEMORY_SUBSCRIPTIONS.get(user_sub) or {}
        row = {
            **existing,
            "render_user_id": user_sub,
            "plan_id": plan_id,
            "period_id": period_id,
            "status": "active",
            "current_period_start": start.isoformat(),
            "current_period_end": end.isoformat(),
            "razorpay_subscription_id": razorpay_subscription_id or existing.get("razorpay_subscription_id"),
            "razorpay_payment_id": razorpay_payment_id or existing.get("razorpay_payment_id"),
            "paid_count": int(existing.get("paid_count") or 0) + 1,
            "updated_at": start.isoformat(),
        }
        _MEMORY_SUBSCRIPTIONS[user_sub] = row
        if row["razorpay_subscription_id"]:
            _MEMORY_SUBS_BY_RZP[row["razorpay_subscription_id"]] = user_sub
        if is_supabase_configured():
            self._supabase_insert("pricing_subscriptions", row, upsert_on="render_user_id")
        return row

    def update_subscription_status(
        self,
        *,
        razorpay_subscription_id: str,
        status: str,
        current_period_end: str | None = None,
        cancelled_at: str | None = None,
        failure_reason: str | None = None,
    ) -> dict | None:
        user_sub = _MEMORY_SUBS_BY_RZP.get(razorpay_subscription_id)
        if not user_sub:
            return None
        row = _MEMORY_SUBSCRIPTIONS.get(user_sub)
        if not row:
            return None
        # Stale-write guard: after a plan change the user_sub row points at
        # the new subscription. Webhooks for the prior (cancelled) sub_id
        # must not overwrite the new row.
        if row.get("razorpay_subscription_id") != razorpay_subscription_id:
            return None
        row["status"] = status
        if current_period_end:
            row["current_period_end"] = current_period_end
        if cancelled_at:
            row["cancelled_at"] = cancelled_at
        if failure_reason:
            row["failure_reason"] = failure_reason
        row["updated_at"] = _now_iso()
        if is_supabase_configured():
            self._supabase_update(
                "pricing_subscriptions",
                {k: v for k, v in {
                    "status": status,
                    "current_period_end": current_period_end,
                    "cancelled_at": cancelled_at,
                    "failure_reason": failure_reason,
                    "updated_at": row["updated_at"],
                }.items() if v is not None},
                where=("razorpay_subscription_id", razorpay_subscription_id),
            )
        return row

    def get_subscription(self, *, user_sub: str) -> dict | None:
        return _MEMORY_SUBSCRIPTIONS.get(user_sub)

    def get_subscription_by_razorpay_id(self, *, razorpay_subscription_id: str) -> dict | None:
        user_sub = _MEMORY_SUBS_BY_RZP.get(razorpay_subscription_id)
        if not user_sub:
            return None
        row = _MEMORY_SUBSCRIPTIONS.get(user_sub)
        if row and row.get("razorpay_subscription_id") != razorpay_subscription_id:
            return None
        return row

    # ────────────────────────── plan cache ──────────────────────────

    def get_cached_plan_id(self, *, period_id: str, amount: int) -> str | None:
        key = f"{period_id}:{int(amount)}"
        if key in _MEMORY_PLAN_CACHE:
            return _MEMORY_PLAN_CACHE[key]
        if is_supabase_configured():
            try:
                from website.core.persist import get_supabase_scope

                scoped = get_supabase_scope()
                if scoped:
                    repo, _ = scoped
                    response = (
                        repo._client.table("pricing_plan_cache")
                        .select("razorpay_plan_id")
                        .eq("cache_key", key)
                        .limit(1)
                        .execute()
                    )
                    if response.data:
                        plan_id = response.data[0]["razorpay_plan_id"]
                        _MEMORY_PLAN_CACHE[key] = plan_id
                        return plan_id
            except Exception as exc:
                logger.warning("Plan cache lookup failed for %s: %s", key, exc)
        return None

    def cache_plan_id(self, *, period_id: str, amount: int, razorpay_plan_id: str) -> None:
        key = f"{period_id}:{int(amount)}"
        _MEMORY_PLAN_CACHE[key] = razorpay_plan_id
        if is_supabase_configured():
            self._supabase_insert(
                "pricing_plan_cache",
                {
                    "cache_key": key,
                    "period_id": period_id,
                    "amount": int(amount),
                    "razorpay_plan_id": razorpay_plan_id,
                },
                upsert_on="cache_key",
            )

    # ────────────────────────── refunds ──────────────────────────

    def record_refund(
        self,
        *,
        razorpay_refund_id: str,
        razorpay_payment_id: str | None,
        payment_id: str | None,
        render_user_id: str | None,
        amount: int,
        status: str,
        speed: str | None = None,
        notes: dict | None = None,
    ) -> dict:
        row = {
            "razorpay_refund_id": razorpay_refund_id,
            "razorpay_payment_id": razorpay_payment_id,
            "payment_id": payment_id,
            "render_user_id": render_user_id,
            "amount": int(amount),
            "currency": "INR",
            "status": status,
            "speed": speed,
            "notes": notes or {},
            "updated_at": _now_iso(),
        }
        existing = _MEMORY_REFUNDS.get(razorpay_refund_id) or {}
        if not existing:
            row["created_at"] = row["updated_at"]
        merged = {**existing, **row}
        _MEMORY_REFUNDS[razorpay_refund_id] = merged
        if is_supabase_configured():
            self._supabase_insert("pricing_refunds", merged, upsert_on="razorpay_refund_id")
        return merged

    def deduct_pack_credits(self, *, user_sub: str, meter: str, quantity: int) -> dict[str, int]:
        wallet = _MEMORY_BALANCES.setdefault(user_sub, {})
        new_balance = max(0, int(wallet.get(meter, 0)) - int(quantity))
        wallet[meter] = new_balance
        if is_supabase_configured():
            try:
                from website.core.persist import get_supabase_scope

                scoped = get_supabase_scope(user_id_override=user_sub)
                if scoped:
                    repo, _ = scoped
                    repo._client.rpc(
                        "pricing_deduct_pack_credits",
                        {"p_render_user_id": user_sub, "p_meter": meter, "p_quantity": int(quantity)},
                    ).execute()
            except Exception as exc:
                logger.warning("deduct_pack_credits supabase failed for user=%s meter=%s: %s", user_sub, meter, exc)
        return dict(wallet)

    # ────────────────────────── disputes ──────────────────────────

    def record_dispute(
        self,
        *,
        razorpay_dispute_id: str,
        razorpay_payment_id: str | None,
        payment_id: str | None,
        render_user_id: str | None,
        amount: int,
        phase: str,
        reason_code: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        row = {
            "razorpay_dispute_id": razorpay_dispute_id,
            "razorpay_payment_id": razorpay_payment_id,
            "payment_id": payment_id,
            "render_user_id": render_user_id,
            "amount": int(amount),
            "currency": "INR",
            "phase": phase,
            "reason_code": reason_code,
            "payload": payload or {},
            "updated_at": _now_iso(),
        }
        existing = _MEMORY_DISPUTES.get(razorpay_dispute_id) or {}
        if not existing:
            row["created_at"] = row["updated_at"]
        merged = {**existing, **row}
        _MEMORY_DISPUTES[razorpay_dispute_id] = merged
        if is_supabase_configured():
            self._supabase_insert("pricing_disputes", merged, upsert_on="razorpay_dispute_id")

        if render_user_id:
            if phase in {"created", "under_review", "action_required"}:
                _DISPUTE_FROZEN.add(render_user_id)
            elif phase in {"won", "closed"}:
                _DISPUTE_FROZEN.discard(render_user_id)
        return merged

    def is_user_dispute_frozen(self, *, user_sub: str) -> bool:
        return user_sub in _DISPUTE_FROZEN

    # ─────────────────── payment_events / idempotency ───────────────────

    def event_already_processed(self, *, event_id: str) -> bool:
        if event_id in _MEMORY_EVENTS:
            return True
        if is_supabase_configured():
            try:
                from website.core.persist import get_supabase_scope

                scoped = get_supabase_scope()
                if not scoped:
                    return False
                repo, _ = scoped
                response = (
                    repo._client.table("pricing_payment_events")
                    .select("event_id")
                    .eq("event_id", event_id)
                    .limit(1)
                    .execute()
                )
                if response.data:
                    _MEMORY_EVENTS[event_id] = {"event_id": event_id}
                    return True
            except Exception as exc:
                logger.warning("Event idempotency check failed for %s: %s", event_id, exc)
        return False

    def record_event(self, *, event_id: str, event_type: str, payment_id: str | None, payload: dict) -> dict:
        row = {
            "event_id": event_id,
            "event_type": event_type,
            "payment_id": payment_id,
            "payload": payload,
            "created_at": _now_iso(),
        }
        _MEMORY_EVENTS[event_id] = row
        if is_supabase_configured():
            self._supabase_insert("pricing_payment_events", row)
        return row

    # ─────────────────────── supabase helpers ───────────────────────

    def _supabase_insert(self, table: str, row: dict, *, upsert_on: str | None = None) -> None:
        try:
            from website.core.persist import get_supabase_scope

            scoped = get_supabase_scope()
            if not scoped:
                return
            repo, _ = scoped
            if upsert_on:
                repo._client.table(table).upsert(row, on_conflict=upsert_on).execute()
            else:
                repo._client.table(table).insert(row).execute()
        except Exception as exc:
            logger.warning("Supabase insert into %s failed: %s", table, exc)

    def _supabase_update(self, table: str, row: dict, *, where: tuple[str, str]) -> None:
        try:
            from website.core.persist import get_supabase_scope

            scoped = get_supabase_scope()
            if not scoped:
                return
            repo, _ = scoped
            col, value = where
            repo._client.table(table).update(row).eq(col, value).execute()
        except Exception as exc:
            logger.warning("Supabase update on %s failed: %s", table, exc)


def get_pricing_repository() -> PricingRepository:
    return PricingRepository()


def reset_memory_state_for_tests() -> None:
    """Test hook — clears in-memory state between tests."""
    _MEMORY_PROFILES.clear()
    _MEMORY_PAYMENTS.clear()
    _MEMORY_BALANCES.clear()
    _MEMORY_SUBSCRIPTIONS.clear()
    _MEMORY_SUBS_BY_RZP.clear()
    _MEMORY_EVENTS.clear()
    _MEMORY_REFUNDS.clear()
    _MEMORY_DISPUTES.clear()
    _MEMORY_PLAN_CACHE.clear()
    _DISPUTE_FROZEN.clear()
