# Test Plan — `user_home`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`user_home`.
Module path: `website/features/user_home/`.
Risk tier: **Moderate** (signed-in landing surface).

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| Shell + header injection | `index.html` (via `_render_with_shell`) |
| Signed-in vs anonymous gating | `js/home.js` |
| Summarize launcher / phone-collect modal | `js/home.js` |
| Recent-zettels strip | `js/home.js` |
| Loader animation | shared loader module |
| Nav into /home/zettels|kastens|rag | nav links |

## Tasks

| ID | P | Task |
|---|---|---|
| UH-01 | P1 | Signed-in/signed-out smoke + redirect to landing if anonymous |
| UH-02 | P1 | Visual regression baseline (Playwright `toHaveScreenshot`, 0.2 threshold) |
| UH-03 | P1 | Color-rule conformance — no purple HSL 250-290 / `#A78BFA`-class; teal accent only; static + computed-style scan |
| UH-04 | P1 | Mobile UA regex routes to `/m/` (Pixel + iPhone UA strings) |
| UH-05 | P2 | **D-3 PIVOT (2026-05-12):** phone-collect modal was removed from `index.html`; UH-05 now tests the quota-exhausted resume flow at `home.js:846-851` — `POST /api/summarize` 402 with `code:quota_exhausted` must invoke `window.ZKPricing.openPurchase`. Implemented at `tests/integration/browser/test_user_home_quota.py`. |
| UH-06 | P2 | axe-core WCAG 2.2 AA scan |
| UH-07 | P2 | Shell composition regression — broken header/footer must not hide auth regression |
| UH-08 | P3 | Synthetic signed-in home monitor — **OPERATIONAL DEFER (2026-05-12):** lives as a `mcp__scheduled-tasks` cron, not in the pytest suite. See § UH-08 below for the canonical payload. |

## Execution order
UH-01 → UH-03 → UH-04 → UH-02 → UH-07 → UH-05 → UH-06 → UH-08

## Industry standards (≤5y)
- Playwright Visual Comparisons
- Playwright Accessibility Testing
- OWASP API1:2023 / API2:2023

## Live-test policy
Mocked auth in CI. `--live` staging. Production read-only `GET /home` unauthenticated (verifies redirect-to-landing).

## UH-08 — synthetic signed-in home monitor (operational)

Runs outside the pytest suite as a scheduled task. Cron payload (paste into a
`mcp__scheduled-tasks__create_scheduled_task` invocation against the operator
agent):

- **cadence:** every 15 minutes during business hours, hourly off-hours
- **probe:** `GET https://zettelkasten.in/home` with a pre-minted Supabase
  session cookie + `User-Agent` desktop UA
- **assertions:** HTTP 200; response body contains `id="home-vault"`; latency
  P95 < 2 s
- **on failure:** post to `SLACK_WEBHOOK_APP_ERRORS` with the captured
  status/body/timing; auto-page if 3 consecutive failures

This is intentionally NOT in `tests/` because it depends on (a) a long-lived
service-role-minted session, (b) a live production target, and (c) external
schedulers; mixing those into pytest collection violates the unit/integration
isolation rule.
