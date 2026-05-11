# WAVE-A Phase 0 — Discovery Findings (2026-05-11)

Verified against live source. Three plan amendments required before execution.

## Confirmed (no plan change)

| Item | File:line | Evidence |
|---|---|---|
| `verify_payment_signature(*, order_id, payment_id, signature, secret=None) -> bool` uses `hmac.compare_digest` | `razorpay_client.py:58-67` | `hmac.compare_digest(expected, signature or "")` |
| `verify_subscription_signature(*, payment_id, subscription_id, signature, secret=None) -> bool` uses `compare_digest` | `razorpay_client.py:70-79` | same |
| `verify_webhook_signature(*, body, signature, secret=None) -> bool` uses `compare_digest` | `razorpay_client.py:82-88` | same — UP-02 already-green |
| `reset_client_cache()` exists | `razorpay_client.py:91-99` | UP-23 already-green |
| `event_already_processed(*, event_id)` | `repository.py:759` | Confirmed |
| `record_event(*, event_id, event_type, payment_id, payload)` | `repository.py:787` | Confirmed |
| `_action_key(user_sub, meter, action_id)` positional, returns None if `action_id` falsy | `entitlements.py:81-84` | UP-14 stays valid |
| `_ACTION_GUARD_TTL_SECONDS = 900` | `entitlements.py:13` | 15-min cache TTL |

## Webhook handler count: 26 (NOT 22)

Full `_WEBHOOK_HANDLERS` keys (auto-parametrize from `dict` — plan still works, expected count updates):
- Payments (4): `payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`
- Refunds (3): `refund.created`, `refund.processed`, `refund.failed`
- Disputes (6): `payment.dispute.{created,under_review,action_required,won,lost,closed}`
- Subscriptions (10): `subscription.{authenticated,activated,charged,pending,halted,paused,resumed,cancelled,completed,updated}`
- Invoices (3): `invoice.{paid,partially_paid,expired}`

## AMENDMENT REQUIRED #1 — `_validate_expected_amount` returns HTTP 409, not 400/422

`routes.py:1043-1056` — raises **409 `price_changed`** ONLY when `expected_amount` mismatches. Currency, pack-floor, billing-profile-required, amount-too-low gates live INLINE in the route handler:
- `400 invalid_product` (routes.py:131)
- `400 billing_profile_required` (routes.py:140)
- `400 amount_too_low` (routes.py:147)
- `409 account_frozen` (routes.py:136)

**UP-07 reframe:** five separate gate tests targeting each HTTPException, not one umbrella "tamper test."

## AMENDMENT REQUIRED #2 — `consume_entitlement` is a no-op fail-open stub

`repository.py:84-121`:
- `check_entitlement(...)` → `return True` (fail-open per Phase 9 plan)
- `consume_entitlement(...)` → `return None` no-op
- **Neither calls `billing.pricing_consume_entitlement` RPC.** The SQL body in `12_revert_unauthorized_pricing.sql` is currently UNINVOKED.

**Implication for plan:**
- **UP-12 exactly-once concurrent consume**: cannot validate concurrency because no consume happens. Reframe → assert the stub IS a no-op (regression-guard so accidental enforcement is flagged). Phase-9 fail-closed test stays xfail.
- **UP-13 fail-open regression guard**: monkeypatch target = `repository.PricingRepository.consume_entitlement` (or upstream). Currently any failure won't propagate because the method never raises. Test confirms `consume_entitlement(...)` returns `None` and does NOT raise.
- **UP-15 plan-tier matrix**: cannot test live quotas because there is no enforcement. Reframe → catalog-level invariant test only: assert `get_public_catalog()` exposes Free 2/10/30, Basic 5/30/50, Max 30/100/200 quotas and plan_ids == {free, basic, max}. Add `xfail(reason="Phase-9 pending")` tests for live quota enforcement that auto-flip when Phase 9 lands.
- **UP-16 no-seed**: still testable — assert no rows in `billing.pricing_entitlement_usage` for a fresh user.

## AMENDMENT REQUIRED #3 — Golden md5 target file

Candidates found:
- `supabase/website/_v2/06_billing_schema.sql:217` — original schema-install body (signature `(uuid, text, text)`)
- `supabase/website/_v2/11_post_install.sql:48` — "default-to-free" body (the unauthorized change that was reverted)
- `supabase/website/_v2/12_revert_unauthorized_pricing.sql:35` — **canonical restoration**: "Restore the original pricing_consume_entitlement body (verbatim copy of 06_billing_schema.sql lines 217-263)"

`tests/unit/supabase_v2/test_schema_files.py` already exists — likely has prior md5 logic. **Decision target: file `12_revert_unauthorized_pricing.sql`** (the canonical anti-tamper anchor). Compute md5 of the file in Phase 1.6.

## Other confirmed

- `_DISPUTE_FROZEN` is module-level set; freeze on `created/under_review/action_required/lost`, clear on `won/closed` (repository.py:745-755)
- `pyrespx`/`responses` availability: TODO — Phase 0.10 unrun. Add to dev requirements if missing.
- Razorpay test-mode webhook secret env var: TODO — Phase 0.9 unrun. Need operator confirmation.

## Plan amendment summary

| Plan task | Original | Amended |
|---|---|---|
| UP-02 | Test `compare_digest` usage | Keep — already-green regression lock |
| UP-06 | 22 handlers expected | Auto-parametrized; expect ~26 |
| UP-07 | Single "amount tamper" test | Split into 5 gate-specific tests (409 price_changed, 400 invalid_product, 400 billing_profile_required, 400 amount_too_low, 409 account_frozen) |
| UP-12 | Concurrent-consume race | Reframe as stub-no-op regression + xfail Phase-9 fail-closed |
| UP-13 | Fail-open vs Phase-9 | Mock target = `PricingRepository.consume_entitlement`; assert returns None, no raise |
| UP-15 | Plan-tier live quotas | Catalog-level invariant + xfail live-quota tests |
| UP-16 | No-seed invariant | Unchanged |
| UP-25 | Golden md5 | Target file confirmed: `supabase/website/_v2/12_revert_unauthorized_pricing.sql` |
| UP-26 | Retired `public.pricing_*` grep | Extend grep to also catch `pricing_consume_entitlement(text, text, text)` legacy v1 signature |
