# Test Plan — `web_monitor`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`web_monitor`.
Module path: `website/features/web_monitor/`.
Risk tier: **High** (silent monitoring loss = operational blind spot).

## Minor modules / sub-flows

| Sub-module | File | Sub-flow |
|---|---|---|
| Aggregate router export | `__init__.py` | mount surface |
| Outbound app errors | `App_Errors.py` (`SlackMessage`, `post_to_app_errors`, `notify_app_error`, `app_errors_healthz`) | fan-out + healthz |
| Inbound DO webhook | `DO_Alerts.py` (`POST /digitalocean`, `_severity`, `DOAlertPayload`, `do_alerts_healthz`) | classify → outbound Slack |
| Outbound user activity + payment webhook | `User_Activity.py` (`notify_new_signup`, `notify_pricing_visit`, `notify_payment`, `POST /payment`, `_mask_email`, `_client_ip`, `user_activity_healthz`) | signup/pricing/payment + PII masking |

## Tasks

| ID | P | Task |
|---|---|---|
| WM-01 | P1 | Failure-isolation contract — global exception handler returns user response even when `post_to_*` raises/timeouts |
| WM-02 | P1 | Inbound webhook auth + payload validation — `POST /digitalocean` and `POST /payment` reject unauthenticated, oversized/malformed JSON, boundary cases |
| WM-03 | P1 | Webhook signature + replay protection — HMAC + timestamp window (≤5 min); reject stale/duplicate IDs (constant-time compare) |
| WM-04 | P1 | PII redaction — `_mask_email` correctness; no raw email/IP/token in Slack payload across all `notify_*` |
| WM-05 | P1 | Slack 429 retry-after + backoff — exponential backoff, max retries, circuit-breaker after N consecutive failures |
| WM-06 | P2 | Multi-channel routing isolation — failure on `post_to_app_errors` does not block `post_to_user_activity` |
| WM-07 | P2 | Outbound rate-limit / bounded queue — burst of N events does not block FastAPI request loop |
| WM-08 | P2 | Inbound payload fuzzing — Hypothesis/schemathesis on `DOAlertPayload` + payment webhook (4xx never 5xx) |
| WM-09 | P2 | Severity classifier (`_severity`) table-driven unit tests |
| WM-10 | P2 | Structured logging assertions — every alert path emits structured line even when Slack post fails |
| WM-11 | P3 | Synthetic alert canary heartbeat — **DEFERRED: WAVE-D hardening sprint** (operator D-1 decision, 2026-05-12). Rationale: low-yield without a paged-runbook consumer; revisit when on-call rotation is staffed. |
| WM-12 | P3 | Healthz contract smoke (3 `*_healthz` endpoints) |
| WM-13 | P3 | Scheduled `notify_pricing_visit` non-blocking under slow Slack |
| WM-14 | P3 | Config/env validation — boot-time check for `*_WEBHOOK_URL`; degraded-mode behavior when unset |

## Execution order
WM-01 → WM-04 → WM-03 → WM-02 → WM-05 → WM-10 → WM-06 → WM-08 → WM-09 → WM-07 → WM-11 → WM-12 → WM-13 → WM-14

## Industry standards (≤5y)
- OWASP Top 10:2021 A09 — Security Logging & Monitoring Failures
- OWASP Logging Cheat Sheet
- Slack — Rate limits (incoming webhooks, 429 + Retry-After)
- Slack — Handling Rate Limits (exponential backoff)
- Webhook Security Guide — HMAC + Replay Protection
- SRE Book — Cascading Failures (foundational)

## MCP / Chrome usage
- Pytest + `respx`/`responses` for HTTP-mocked Slack calls (no Chrome needed)
- `mcp__scheduled-tasks` for WM-11 canary heartbeat verification
- `mcp__github` for CI workflow assertions

## WAVE-D Phase 1 — implementation notes (2026-05-12)

* **WM-03 surgical fix landed**: `DO_Alerts.py` shared-secret compare now uses `hmac.compare_digest` (constant-time). Regression-guarded by `tests/unit/web_monitor/test_do_webhook_auth.py::test_digitalocean_webhook_uses_compare_digest_not_eq`.
* **WM-05 + WM-07 merged**: `website/features/web_monitor/_slack_client.py` adds `post_with_retry` (stamina retry, Retry-After honoring, exp+jitter for transients) and `fire_and_forget` (asyncio.Semaphore(8) per worker + strong-ref task set). All 3 channel `post_to_*` helpers now delegate to it.
* **WM-08 fix-in-place**: DO webhook now catches `pydantic.ValidationError` and returns 400 instead of 5xx — addresses fuzz regression discovered while authoring `test_payload_fuzz.py`.
* **WM-14 wired**: `_env_validation.log_web_monitor_env_warnings()` runs once at `create_app()` boot; warns per unset `SLACK_WEBHOOK_*`.
* **WM-15**: source-of-truth column is `core.profiles.display_name` (NOT `full_name`). Spec citation was stale post-DB-v2; verified at `supabase/website/_v2/01_core_schema.sql:7`. Resolution helper `_resolve_full_name(display_name, email)` falls back to email-localpart, then em-dash.
* **WM-16**: in-module ISO-3166 mapping (`_country.py`, ~50 entries + `Unknown (XX)` fallback). No new dependency.
* **WM-11 deferred** per operator D-1; documented above.


Mocked Slack in CI. `--live` staging only. Production read-only: `GET /digitalocean_healthz`, `GET /app_errors_healthz`, `GET /user_activity_healthz`.
