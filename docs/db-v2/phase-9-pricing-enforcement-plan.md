# Phase 9 — Pricing Enforcement Plan (future iteration)

> Authored 2026-05-11. NOT scheduled. Cited from research synthesis during Phase 8.0 closeout.
> Operator-locked discipline: per `feedback_pricing_module_authority.md`, no part of Phase 9
> seeds `billing.*` data, alters `billing.pricing_consume_entitlement` body (golden md5),
> or invents plan names without operator-approved per-row sign-off.

## Goal

Move from current "fail-open" entitlement gate (Phase 8.0 documented in user_pricing/repository.py)
to fully-enforced v2 entitlement per `docs/research/pricing1.md` (Free 2/10/30 daily/weekly/monthly
zettels; Basic 5/30/50; Max 30/100/200) without breaking real users.

## 6-Phase rollout

### Phase 9.A — Schema additions (additive only)

- New: `billing.pricing_plan_entitlements_v3` (multi-period: day/week/month/total caps per feature)
- New: `billing.pricing_usage_counters_v3` (rolling counter table; per profile_id × feature × period)
- New: `billing.pricing_decisions_audit` (every entitlement check logged for backfill diagnosis)

All additive. Existing `billing.pricing_consume_entitlement` body untouched (golden md5 enforced).

### Phase 9.B — pricing_consume_entitlement_v3 RPC alongside v2

- New: `billing.pricing_consume_entitlement_v3(p_profile_id uuid, p_feature text, p_unit text, p_quantity int)`
- Returns: `(allowed bool, reason text, retry_after_seconds int)`
- Reads multi-period entitlements; updates counters atomically; logs decision
- v2 RPC stays for rollback safety; cutover via env flag in Phase 9.E

### Phase 9.C — Default subscription seeding (operator-approved per row)

- Trigger: `auth.users INSERT` → seed `billing.pricing_subscriptions` Free row for new signup
- Backfill existing 2 users (Naruto + Zoro) with Free subscriptions — operator approves each by name
- NO bulk seeding; each row inserted via operator-confirmed migration

### Phase 9.D — Shadow mode (7-14 days)

- App calls v3 in dry_run mode (logs decision + would-have-blocked count) but always returns True
- Compare v3 dry_run results against v2 fail-open (which returns True) → expect 100% match for free users with 0 usage
- Watch for false-blocks (would have blocked legitimate use)

### Phase 9.E — Hard cutover

- Env flag `PRICING_ENFORCEMENT=hard` switches `consume_entitlement` to call v3 (not v2)
- 402 Payment Required + `quota_exhausted` body when blocked
- Per-feature gradual rollout (start with low-volume features; expand after 24h soak)

### Phase 9.F — Razorpay webhook → subscription upgrade

- Webhook handler upserts `billing.pricing_subscriptions` on payment_completed event
- Plan IDs from operator-approved registry (NOT invented in code)
- Handles Basic + Max tier upgrades + downgrades

## Citations

- Lago — open-source metering: https://github.com/getlago/lago
- OpenMeter Entitlements: https://openmeter.io/docs/billing/entitlements/entitlement
- Stripe Meters: https://docs.stripe.com/billing/subscriptions/usage-based/recording-usage
- Neon — Rate Limiting in Postgres: https://neon.com/guides/rate-limiting
- Schematic feature flags: https://schematichq.com/blog/guide-how-to-use-feature-flags-to-manage-entitlements-without-writing-code
- LaunchDarkly Entitlements: https://launchdarkly.com/blog/how-to-manage-entitlements-with-feature-flags/
- Monetizely Grandfathering: https://www.getmonetizely.com/articles/grandfathering-vs-forced-migration-the-strategic-approach-to-price-changes-for-existing-customers
- Postgres ALTER FUNCTION: https://www.postgresql.org/docs/current/sql-alterfunction.html

## Operator-locked invariants (per pricing_module_authority memory)

- `billing.pricing_consume_entitlement` body protected by golden md5 — NEVER modified
- `billing.*` data NEVER seeded without per-row operator approval in chat
- Plan names from `docs/research/pricing1.md` ONLY — code never invents new plan names
- 402 quota_exhausted is the operator-approved hard-block response — not changed without sign-off
