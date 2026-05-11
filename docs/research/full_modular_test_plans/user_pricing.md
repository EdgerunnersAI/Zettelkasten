# Test Plan — `user_pricing`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`user_pricing` (lines 233-244).
Module path: `website/features/user_pricing/`.
Risk tier: **Critical** (canonical-truth payment + entitlement).

## Locked invariants (CLAUDE.md "Pricing Module Authority")
- NEVER seed entitlements / NEVER auto-subscribe / NEVER invent plan names
- Plan tiers: Free 2/10/30 · Basic 5/30/50 · Max 30/100/200
- `billing.pricing_consume_entitlement` body protected by golden md5
- Currently **fail-open**; Phase 9 will flip to fail-closed

## Minor modules / sub-flows

| Sub-module | File / surfaces |
|---|---|
| Catalog | `catalog.py` (`get_public_catalog` lru, `find_product`, `_generated_custom_pack`, `_normalize_custom_quantity`) |
| Billing profile | `routes.py:101-118`, `repository.py:125-178`, `BillingProfileRequest` |
| One-time order | `routes.py:124-186`, `_validate_expected_amount`, `_order_notes`, `attach_provider_order`, `mark_payment_paid` |
| Subscription create/change/cancel/status | `routes.py:192-446`, `get_or_create_plan`, `create_or_update_subscription`, `activate_subscription`, `update_subscription_status` |
| Payment verify | `routes.py:452-490`, `verify_payment_signature`, `verify_subscription_signature`, `_apply_fulfillment` |
| Webhook (canonical truth) | `routes.py:496-831` — 22 `_HANDLERS` (`payment.*`, `order.paid`, `subscription.*`, `refund.*`, `dispute.*`, `invoice.*`), `verify_webhook_signature`, `event_already_processed`, `record_event` |
| Entitlements | `entitlements.py` — `require_entitlement`, `consume_entitlement`, `PricingQuotaError`, action-key cache |
| Pack credits / refunds / disputes | `add_pack_credits`, `deduct_pack_credits`, `record_refund`, `record_dispute`, `is_user_dispute_frozen` |
| Razorpay client | `razorpay_client.py` — key/secret/webhook-secret, lru client, signature helpers, plan caching |
| Browser launcher | `js/purchase_launcher.js` — `ensureBillingProfile`, `createCheckout`, `openRazorpayCheckout`, resume pending |

## Tasks

| ID | P | Task |
|---|---|---|
| UP-01 | P1 | Webhook signature suite — valid HMAC-SHA256 over **raw bytes**, header mismatch, missing header, wrong secret, body mutated between hash & parse |
| UP-02 | P1 | Constant-time compare via `hmac.compare_digest` in all `verify_*_signature` |
| UP-03 | P1 | Replay / idempotency — same `event.id` ×2/×5/interleaved → single `record_event` row, no double credit/activation |
| UP-04 | P1 | Out-of-order delivery — `subscription.charged` before `.activated`; `payment.captured` before `.authorized`; `refund.processed` before `.created` |
| UP-05 | P1 | Partial-commit recovery — DB error after `record_event` before `_apply_fulfillment` and vice versa; retry completes w/o double-consume |
| UP-06 | P1 | Handler matrix completeness — 22 `_HANDLERS` each get unit test: happy + unknown subtype + missing payload |
| UP-07 | P1 | `_validate_expected_amount` tamper — mismatched paise, custom-pack below floor, currency mismatch → 400/422, no Razorpay call |
| UP-08 | P1 | Verify-order signature — `razorpay_payment_id\|razorpay_order_id` HMAC; wrong → 400; correct + already-fulfilled idempotent |
| UP-09 | P1 | Subscription change Free→Basic→Max, Max→Basic, cancel-rejoin — NEVER seeds entitlements/invents plan names/auto-subscribes |
| UP-10 | P1 | Cancel paths — `cancel_at_period_end` vs immediate; `subscription.cancelled` reconciliation; refund window |
| UP-11 | P1 | `get_or_create_plan` race — concurrent create_subscription same `(period_id,amount)` → no duplicate Razorpay plan |
| UP-12 | P1 | Exactly-once `consume_entitlement` — concurrent calls at quota=1 → exactly one succeeds, one gets 402 quota_exhausted |
| UP-13 | P1 | Fail-open regression guard — current behavior on RPC failure (do not block); paired Phase-9 fail-closed pending xfail |
| UP-14 | P1 | Action-key cache correctness — `_action_key`/`_is_cached` dedup, TTL respected, does not bridge across users |
| UP-15 | P1 | Plan-tier matrix — Free/Basic/Max meters (zettels/RAG/Kasten) — table-driven, never invent tiers |
| UP-16 | P1 | No-seed invariant — creating user does NOT pre-populate entitlements; first consume materializes |
| UP-17 | P1 | `record_refund` + `deduct_pack_credits` — partial deducts proportionally; full freezes user; idempotent on duplicate `.processed` |
| UP-18 | P1 | Dispute lifecycle — `dispute.{created,won,lost,closed}` via `_dispute_handler(phase=...)`; freeze applied/lifted |
| UP-19 | P1 | `_scope(user_sub)` BOLA — A cannot read/modify B's profile/payment/subscription; UUID-leak assertions |
| UP-20 | P1 | Cross-tenant webhook spoof — valid sig but `notes.user_sub` references another tenant → trust `razorpay_*_id` lookup not notes |
| UP-21 | P1 | Secret-scan of `purchase_launcher.js` (CI grep) — no KEY_SECRET / webhook secret; only public `key_id` templated |
| UP-22 | P1 | Server response sanitization — `_public_payment`, `_public_subscription`, `_checkout_payload` never leak secrets / internal notes / raw provider errors |
| UP-23 | P1 | Razorpay client init isolation — `reset_client_cache` works in tests; lru does not hold stale across rotation |
| UP-24 | P1 | Launcher auth-token — token from localStorage only, never sent to Razorpay; logged-out → clean "please sign in" |
| UP-25 | P1 | Golden-md5 of `billing.pricing_consume_entitlement` body — CI fails if RPC body drifts |
| UP-26 | P1 | Pre-DROP CI grep — fails build if any reference to retired `public.pricing_*` tables (Phase-8 recurrence guard) |
| UP-27 | P2 | Webhook 2xx within 20s (defer heavy work) — Razorpay/Stripe retry contract |
| UP-28 | P2 | `attach_provider_order` idempotency — same razorpay_order_id ×2 = no dup payment record |
| UP-29 | P2 | Multi-period (Phase 9 prep) — monthly vs yearly counter reset; pending xfail until Phase 9 |
| UP-30 | P2 | Subscription halted/paused/resumed — gating during paused; resume restores w/o re-charge |
| UP-31 | P2 | Invoice expired — `_h_invoice_expired` does not leave ghost active subscription |
| UP-32 | P2 | `ensureBillingProfile` retry — 401/403/net failure UX; inline phone modal short-circuit |
| UP-33 | P2 | `resumePendingPurchase` — page-reload mid-checkout resumes same `payment_id`, no duplicate order |
| UP-34 | P2 | Webhook observability — every `_h_*` emits structured log w/ `event.id,event.type,user_sub,payment_id,outcome` |

## Execution order
UP-01 → UP-02 → UP-03 → UP-05 → UP-04 → UP-06 → UP-25 → UP-26 → UP-12 → UP-13 → UP-14 → UP-15 → UP-16 → UP-07 → UP-08 → UP-09 → UP-10 → UP-11 → UP-17 → UP-18 → UP-19 → UP-20 → UP-21 → UP-22 → UP-23 → UP-24 → P2 sweep.

## Industry standards (≤5y)
- Razorpay Webhooks (validate-test, best-practices) — HMAC-SHA256 raw body
- Stripe Webhooks — idempotency, 20s ack, created-timestamp ordering
- Hookdeck Webhook Idempotency Guide
- Svix Razorpay Review (replay gap)
- PCI SSC SAQ-A v4.0 iFrame/Redirect criteria (2024-2025)
- Schematic Entitlement System; Lago Feature Entitlements
- OWASP API1:2023 BOLA

## Live-test policy
Mocked Razorpay in CI. `--live` against Razorpay **test mode** in staging only. Production read-only `GET /api/pricing/catalog` and `GET /api/pricing/status` allowed. NEVER replay real webhooks against production.
