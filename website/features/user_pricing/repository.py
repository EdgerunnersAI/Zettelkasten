"""Persistence facade for pricing state (DB v2 billing schema only).

The concrete production schema is provided by the v2 migrations under
``supabase/website/_v2/06_billing_schema.sql`` and
``supabase/website/_v2/30_billing_pricing_active_plan.sql``. This facade
stays small so route code can be tested without a live database.

**v2-only since Phase 8.0.2 (2026-05-10).** Every previous "v2 first, then v1
fallback" branch was deleted. Both production users authenticate as Supabase
Auth UUIDs; the v1 ``public.pricing_*`` surface is unreachable. See
``docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md`` for the
purge plan and ``docs/db-v2/phase-9-pricing-enforcement-plan.md`` for the
multi-period enforcement plan that will replace the current fail-open
``check_entitlement`` / ``consume_entitlement`` stubs.

In-memory dicts remain as a graceful fallback whenever the v2 client cannot
be reached (network blip, missing UUID auth, scope-helper raises) so ingest
never blocks real users on infra hiccups. Webhook-replay safety is handled
by the unique-constraint guarantees on ``billing.pricing_*`` tables.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from website.core.supabase_v2.client import is_v2_configured as is_supabase_configured
from website.features.user_pricing.models import Meter

# ``is_supabase_configured`` is re-exported only for backward-compatibility
# with the existing ``test_razorpay_routes`` fixture (Phase 3.2 alias).
# The v2-only routing code path in this module does NOT consult it — the
# ``_scope()`` helper below is the single gate. Tests that monkeypatch this
# symbol to ``lambda: False`` get the in-memory mirror behaviour because
# ``_scope()`` independently returns None when ``get_billing_scope`` raises.
__all__ = (
    "PricingRepository",
    "get_pricing_repository",
    "reset_memory_state_for_tests",
    "is_supabase_configured",
)

logger = logging.getLogger(__name__)

_MEMORY_PROFILES: dict[str, dict] = {}
_MEMORY_PAYMENTS: dict[str, dict] = {}
_MEMORY_BALANCES: dict[str, dict[str, int]] = {}
# In-memory mirrors keep the legacy ``render_user_id`` key name to preserve
# the dict shape consumed by ``website/features/user_pricing/routes.py`` —
# the v2 schema migration moved the SQL column to ``profile_id``, but the
# Python-side dict is route-layer contract.
_MEMORY_SUBSCRIPTIONS: dict[str, dict] = {}  # keyed by user_sub (= profile_id UUID string)
_MEMORY_SUBS_BY_RZP: dict[str, str] = {}      # razorpay_subscription_id -> user_sub
_MEMORY_EVENTS: dict[str, dict] = {}
_MEMORY_REFUNDS: dict[str, dict] = {}         # razorpay_refund_id -> row
_MEMORY_DISPUTES: dict[str, dict] = {}        # razorpay_dispute_id -> row
_MEMORY_PLAN_CACHE: dict[str, str] = {}       # "{period_id}:{amount}" -> razorpay_plan_id

# Set of users with an open dispute — fulfillment is paused while in this set.
_DISPUTE_FROZEN: set[str] = set()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _scope(user_sub: str | None):
    """Resolve (client, profile_id) or return None when v2 is unreachable.

    Hard-fails on non-UUID user_sub (operator-approved Phase 8.0 decision)
    by catching the RuntimeError from get_billing_scope and returning None.
    Callers fall back to the in-memory dict path on None.
    """
    if not user_sub:
        return None
    try:
        from website.core.persist import get_billing_scope

        return get_billing_scope(user_sub)
    except RuntimeError:
        # Non-UUID auth subject: v2 billing path not available.
        return None
    except Exception as exc:  # noqa: BLE001 — defensive: v2 client init may fail
        logger.warning("billing scope acquisition failed for user_sub=%s: %s", user_sub, exc)
        return None


class PricingRepository:
    """v2-only pricing repository (billing schema) with in-memory fallback."""

    # ────────────────────────── entitlements ──────────────────────────

    def check_entitlement(self, *, user_sub: str, meter: Meter, action_id: str | None) -> bool:
        """Currently fail-open per Phase 9 pricing-enforcement plan.

        Multi-period (day/week/month/total) caps require schema work +
        operator-approved entitlement seeding per ``pricing1.md``. Until
        Phase 9 lands ``billing.pricing_consume_entitlement_v3`` with
        multi-period support + plan-row seeds, this returns True.

        See ``docs/db-v2/phase-9-pricing-enforcement-plan.md``.
        """
        logger.debug(
            "pricing check_entitlement called; fail-open until Phase 9",
            extra={"user_sub": user_sub, "meter": str(meter), "action_id": action_id},
        )
        return True

    def consume_entitlement(self, *, user_sub: str, meter: Meter, action_id: str | None) -> None:
        """Fail-open no-op per Phase 9 pricing-enforcement plan.

        Pairs with ``check_entitlement`` above. Will be replaced when
        Phase 9 ships the v3 enforcement RPC; until then, request counters
        remain unincremented and quota is effectively unlimited.
        """
        logger.debug(
            "pricing consume_entitlement called; no-op until Phase 9",
            extra={"user_sub": user_sub, "meter": str(meter), "action_id": action_id},
        )
        return None

    # ───────────────────────── billing profile ─────────────────────────

    def get_billing_profile(self, *, user_sub: str) -> dict | None:
        scoped = _scope(user_sub)
        if not scoped:
            return _MEMORY_PROFILES.get(user_sub)

        client, profile_id = scoped
        try:
            response = (
                client.schema("billing")
                .table("pricing_billing_profiles")
                .select("*")
                .eq("profile_id", str(profile_id))
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else _MEMORY_PROFILES.get(user_sub)
        except Exception as exc:
            logger.warning("Billing profile lookup failed for user=%s: %s", user_sub, exc)
            return _MEMORY_PROFILES.get(user_sub)

    def upsert_billing_profile(self, *, user_sub: str, email: str, phone: str, name: str = "") -> dict:
        # Memory mirror retains the legacy shape (phone, name) for tests +
        # diagnostic surfaces; the v2 row stores only what the schema accepts.
        memory_row = {
            "render_user_id": user_sub,
            "email": email,
            "phone": phone,
            "name": name,
            "updated_at": _now_iso(),
        }
        _MEMORY_PROFILES[user_sub] = memory_row

        scoped = _scope(user_sub)
        if not scoped:
            return memory_row

        client, profile_id = scoped
        v2_row = {
            "profile_id": str(profile_id),
            "email": email,
            "name": name,
            "updated_at": memory_row["updated_at"],
        }
        try:
            response = (
                client.schema("billing")
                .table("pricing_billing_profiles")
                .upsert(v2_row, on_conflict="profile_id")
                .execute()
            )
            return response.data[0] if response.data else v2_row
        except Exception as exc:
            logger.warning("Billing profile upsert failed for user=%s: %s", user_sub, exc)
            return memory_row

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

        scoped = _scope(user_sub)
        if scoped:
            client, profile_id = scoped
            v2_row = {
                "profile_id": str(profile_id),
                "kind": kind,
                "amount": int(amount),
                "amount_paise": int(amount),
                "currency": currency,
                "plan_id": plan_id,
                "period_id": period_id,
                "status": "created",
                "razorpay_order_id": None,
                "razorpay_subscription_id": None,
                "razorpay_payment_id": None,
                "provider_payload": {
                    "payment_id": payment_id,
                    "product_id": product_id,
                    "meter": meter,
                    "quantity": int(quantity) if quantity is not None else None,
                },
                "created_at": row["created_at"],
                "updated_at": row["created_at"],
            }
            self._billing_insert(client, "pricing_orders", v2_row)
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

        # v2 billing.pricing_orders has no payment_id column (uses provider_payload
        # JSON for payment_id linkage). Update by razorpay_order_id when available;
        # otherwise this is a memory-only update until the order is paid.
        scoped = _scope(row.get("render_user_id"))
        if scoped and razorpay_order_id:
            client, _ = scoped
            updates = {k: v for k, v in {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_subscription_id": razorpay_subscription_id,
                "updated_at": row["updated_at"],
            }.items() if v is not None}
            try:
                (
                    client.schema("billing")
                    .table("pricing_orders")
                    .update(updates)
                    .eq("razorpay_order_id", razorpay_order_id)
                    .execute()
                )
            except Exception as exc:
                logger.warning("Billing order attach failed for %s: %s", razorpay_order_id, exc)
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

        scoped = _scope(row.get("render_user_id"))
        if scoped and row.get("razorpay_order_id"):
            client, _ = scoped
            try:
                (
                    client.schema("billing")
                    .table("pricing_orders")
                    .update({
                        "razorpay_payment_id": razorpay_payment_id,
                        "status": "paid",
                        "paid_at": row["paid_at"],
                        "updated_at": row["paid_at"],
                    })
                    .eq("razorpay_order_id", row["razorpay_order_id"])
                    .execute()
                )
            except Exception as exc:
                logger.warning("Billing order mark-paid failed for %s: %s", payment_id, exc)
        return row

    def mark_payment_failed(self, *, payment_id: str, reason: str) -> dict:
        row = _MEMORY_PAYMENTS.setdefault(payment_id, {"payment_id": payment_id})
        row["status"] = "failed"
        row["failure_reason"] = reason
        row["updated_at"] = _now_iso()

        scoped = _scope(row.get("render_user_id"))
        if scoped and row.get("razorpay_order_id"):
            client, _ = scoped
            try:
                (
                    client.schema("billing")
                    .table("pricing_orders")
                    .update({
                        "status": "failed",
                        "failure_reason": reason,
                        "updated_at": row["updated_at"],
                    })
                    .eq("razorpay_order_id", row["razorpay_order_id"])
                    .execute()
                )
            except Exception as exc:
                logger.warning("Billing order mark-failed failed for %s: %s", payment_id, exc)
        return row

    def get_payment_record(self, *, payment_id: str) -> dict | None:
        # billing.pricing_orders has no payment_id column — payment_id lives
        # only in the in-memory mirror + provider_payload JSON. Memory hit
        # is the canonical lookup path.
        return _MEMORY_PAYMENTS.get(payment_id)

    def find_payment_by_razorpay_order(self, *, razorpay_order_id: str) -> dict | None:
        for row in _MEMORY_PAYMENTS.values():
            if row.get("razorpay_order_id") == razorpay_order_id:
                return row

        # Best-effort: any UUID-authed user's scope works for this read since
        # razorpay_order_id is globally unique. Try the first memory row that
        # has a user_sub; otherwise we have no scope to query.
        for row in _MEMORY_PAYMENTS.values():
            user_sub = row.get("render_user_id")
            if not user_sub:
                continue
            scoped = _scope(user_sub)
            if not scoped:
                continue
            client, _ = scoped
            try:
                response = (
                    client.schema("billing")
                    .table("pricing_orders")
                    .select("*")
                    .eq("razorpay_order_id", razorpay_order_id)
                    .limit(1)
                    .execute()
                )
                if response.data:
                    return response.data[0]
            except Exception as exc:
                logger.warning("Billing order lookup by razorpay_order_id failed: %s", exc)
                return None
            break
        return None

    # ─────────────────────────── balances ────────────────────────────

    def add_pack_credits(self, *, user_sub: str, meter: str, quantity: int) -> dict[str, int]:
        wallet = _MEMORY_BALANCES.setdefault(user_sub, {})
        wallet[meter] = int(wallet.get(meter, 0)) + int(quantity)

        scoped = _scope(user_sub)
        if scoped:
            client, profile_id = scoped
            try:
                client.schema("billing").rpc(
                    "pricing_add_pack_credits",
                    {
                        "p_profile_id": str(profile_id),
                        "p_meter": meter,
                        "p_quantity": int(quantity),
                    },
                ).execute()
            except Exception as exc:
                logger.warning("add_pack_credits billing failed for user=%s meter=%s: %s", user_sub, meter, exc)
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

        scoped = _scope(user_sub)
        if scoped:
            client, profile_id = scoped
            v2_row = {
                "profile_id": str(profile_id),
                "plan_id": plan_id,
                "period_id": period_id,
                "status": status,
                "razorpay_subscription_id": razorpay_subscription_id,
                "total_count": row["total_count"],
                "paid_count": int(row["paid_count"] or 0),
                "current_period_start": row["current_period_start"],
                "current_period_end": row["current_period_end"],
                "updated_at": row["updated_at"],
            }
            self._billing_insert(client, "pricing_subscriptions", v2_row, upsert_on="razorpay_subscription_id")
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

        scoped = _scope(user_sub)
        if scoped and row["razorpay_subscription_id"]:
            client, profile_id = scoped
            v2_row = {
                "profile_id": str(profile_id),
                "plan_id": plan_id,
                "period_id": period_id,
                "status": "active",
                "razorpay_subscription_id": row["razorpay_subscription_id"],
                "razorpay_payment_id": row.get("razorpay_payment_id"),
                "paid_count": row["paid_count"],
                "current_period_start": row["current_period_start"],
                "current_period_end": row["current_period_end"],
                "updated_at": row["updated_at"],
            }
            self._billing_insert(client, "pricing_subscriptions", v2_row, upsert_on="razorpay_subscription_id")
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

        scoped = _scope(user_sub)
        if scoped:
            client, _ = scoped
            updates = {k: v for k, v in {
                "status": status,
                "current_period_end": current_period_end,
                "cancelled_at": cancelled_at,
                "failure_reason": failure_reason,
                "updated_at": row["updated_at"],
            }.items() if v is not None}
            try:
                (
                    client.schema("billing")
                    .table("pricing_subscriptions")
                    .update(updates)
                    .eq("razorpay_subscription_id", razorpay_subscription_id)
                    .execute()
                )
            except Exception as exc:
                logger.warning("Billing subscription update failed for %s: %s", razorpay_subscription_id, exc)
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

        # plan-cache is global (not per-user). Use any available scope; if no
        # user_sub is in memory we cannot resolve a v2 client, so memory-only.
        for user_sub in _MEMORY_PROFILES.keys():
            scoped = _scope(user_sub)
            if not scoped:
                continue
            client, _ = scoped
            try:
                response = (
                    client.schema("billing")
                    .table("pricing_plan_cache")
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
        return None

    def cache_plan_id(self, *, period_id: str, amount: int, razorpay_plan_id: str) -> None:
        key = f"{period_id}:{int(amount)}"
        _MEMORY_PLAN_CACHE[key] = razorpay_plan_id

        for user_sub in _MEMORY_PROFILES.keys():
            scoped = _scope(user_sub)
            if not scoped:
                continue
            client, _ = scoped
            self._billing_insert(
                client,
                "pricing_plan_cache",
                {
                    "cache_key": key,
                    "period_id": period_id,
                    "amount": int(amount),
                    "razorpay_plan_id": razorpay_plan_id,
                },
                upsert_on="cache_key",
            )
            return

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
        # render_user_id parameter name is preserved at the call-site contract
        # (route layer), but stored as profile_id in v2. The argument is the
        # user_sub from the auth claim — semantically the v2 profile_id UUID.
        user_sub = render_user_id
        row = {
            "razorpay_refund_id": razorpay_refund_id,
            "razorpay_payment_id": razorpay_payment_id,
            "payment_id": payment_id,
            "render_user_id": user_sub,
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

        scoped = _scope(user_sub)
        if scoped:
            client, profile_id = scoped
            v2_row = {
                "razorpay_refund_id": razorpay_refund_id,
                "razorpay_payment_id": razorpay_payment_id,
                "payment_id": payment_id,
                "profile_id": str(profile_id),
                "amount": int(amount),
                "currency": "INR",
                "status": status,
                "speed": speed,
                "notes": notes or {},
                "updated_at": merged["updated_at"],
            }
            self._billing_insert(client, "pricing_refunds", v2_row, upsert_on="razorpay_refund_id")
        return merged

    def deduct_pack_credits(self, *, user_sub: str, meter: str, quantity: int) -> dict[str, int]:
        wallet = _MEMORY_BALANCES.setdefault(user_sub, {})
        new_balance = max(0, int(wallet.get(meter, 0)) - int(quantity))
        wallet[meter] = new_balance

        scoped = _scope(user_sub)
        if scoped:
            client, profile_id = scoped
            try:
                client.schema("billing").rpc(
                    "pricing_deduct_pack_credits",
                    {
                        "p_profile_id": str(profile_id),
                        "p_meter": meter,
                        "p_quantity": int(quantity),
                    },
                ).execute()
            except Exception as exc:
                logger.warning("deduct_pack_credits billing failed for user=%s meter=%s: %s", user_sub, meter, exc)
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
        # render_user_id parameter name preserved at route boundary; stored
        # as profile_id in v2 (semantically the user_sub UUID).
        user_sub = render_user_id
        row = {
            "razorpay_dispute_id": razorpay_dispute_id,
            "razorpay_payment_id": razorpay_payment_id,
            "payment_id": payment_id,
            "render_user_id": user_sub,
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

        scoped = _scope(user_sub)
        if scoped:
            client, profile_id = scoped
            v2_row = {
                "razorpay_dispute_id": razorpay_dispute_id,
                "razorpay_payment_id": razorpay_payment_id,
                "payment_id": payment_id,
                "profile_id": str(profile_id),
                "amount": int(amount),
                "currency": "INR",
                "phase": phase,
                "reason_code": reason_code,
                "payload": payload or {},
                "updated_at": merged["updated_at"],
            }
            self._billing_insert(client, "pricing_disputes", v2_row, upsert_on="razorpay_dispute_id")

        if user_sub:
            if phase in {"created", "under_review", "action_required"}:
                _DISPUTE_FROZEN.add(user_sub)
            elif phase in {"won", "closed"}:
                _DISPUTE_FROZEN.discard(user_sub)
        return merged

    def is_user_dispute_frozen(self, *, user_sub: str) -> bool:
        return user_sub in _DISPUTE_FROZEN

    # ─────────────────── payment_events / idempotency ───────────────────

    def event_already_processed(self, *, event_id: str) -> bool:
        if event_id in _MEMORY_EVENTS:
            return True

        # Webhook idempotency: any UUID scope works since event_id is globally
        # unique. Fall through memory-only when no scope is reachable.
        for user_sub in _MEMORY_PROFILES.keys():
            scoped = _scope(user_sub)
            if not scoped:
                continue
            client, _ = scoped
            try:
                response = (
                    client.schema("billing")
                    .table("pricing_payment_events")
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

        # Persist to billing.pricing_payment_events without a profile binding —
        # the schema allows profile_id NULL (ON DELETE SET NULL). Try any
        # reachable scope; on failure the in-memory mirror still de-dups.
        for user_sub in _MEMORY_PROFILES.keys():
            scoped = _scope(user_sub)
            if not scoped:
                continue
            client, _ = scoped
            v2_row = {
                "event_id": event_id,
                "event_type": event_type,
                "payment_id": payment_id,
                "payload": payload,
                "created_at": row["created_at"],
            }
            self._billing_insert(client, "pricing_payment_events", v2_row)
            break
        return row

    # ─────────────────────── billing helpers ───────────────────────

    def _billing_insert(
        self,
        client,
        table: str,
        row: dict,
        *,
        upsert_on: str | None = None,
    ) -> None:
        try:
            tbl = client.schema("billing").table(table)
            if upsert_on:
                tbl.upsert(row, on_conflict=upsert_on).execute()
            else:
                tbl.insert(row).execute()
        except Exception as exc:
            logger.warning("Billing insert into %s failed: %s", table, exc)


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
