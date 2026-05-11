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
| WM-11 | P3 | Synthetic alert canary heartbeat |
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

## Live-test policy
Mocked Slack in CI. `--live` staging only. Production read-only: `GET /digitalocean_healthz`, `GET /app_errors_healthz`, `GET /user_activity_healthz`.
