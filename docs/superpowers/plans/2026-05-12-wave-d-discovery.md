# WAVE-D Phase 0 — Discovery Findings

Scope: 3 modules (`web_monitor`, `user_home`, `header`).
Inputs: `docs/research/full_modular_test_plans/{web_monitor,user_home,header}.md`, `docs/research/Full_Features_Test_Strategy1.md`, prior wave plans (A 2026-05-11, B 2026-05-12, C 2026-05-12).
Inspector: Phase-0 discovery subagent. Code-only. No edits.

---

## Module: web_monitor (HIGH tier, 14 tasks)

### 1. File inventory (verified via `smart_outline`)

| File | Lines | Symbols |
|---|---|---|
| `website/features/web_monitor/__init__.py` | 31 | aggregate `router` (mounts the 3 child routers); re-exports `notify_app_error`, `notify_new_signup`, `notify_pricing_visit`, `notify_payment` |
| `website/features/web_monitor/App_Errors.py` | 171 | `SlackMessage` (dataclass, slots), `to_payload`, `post_to_app_errors`, `notify_app_error`, `app_errors_healthz` (GET `/webhooks/monitor/app-errors/healthz`) |
| `website/features/web_monitor/DO_Alerts.py` | 219 | `SlackMessage`, `to_payload`, `post_to_do_alerts`, `DOAlertPayload` (Pydantic), `_severity`, `digitalocean_alert` (POST `/webhooks/monitor/digitalocean`), `do_alerts_healthz` |
| `website/features/web_monitor/User_Activity.py` | 355 | `SlackMessage`, `to_payload`, `post_to_user_activity`, `_client_ip`, `_mask_email`, `notify_new_signup`, `notify_pricing_visit`, `notify_payment`, `payment_webhook` (POST `/webhooks/monitor/payment`), `user_activity_healthz` |

Notes:
- The shared router prefix is `/webhooks/monitor`. App-errors healthz path = `/webhooks/monitor/app-errors/healthz`, DO healthz = `/webhooks/monitor/digitalocean/healthz`, user-activity healthz = `/webhooks/monitor/user-activity/healthz`. Spec WM-12 (3 `*_healthz` endpoints) — **confirmed exact endpoints**.
- Three independent `SlackMessage` dataclass copies (one per channel file) — per-channel design decision documented in `__init__.py` (`# Add a new channel = add a new sibling file; no shared base to coordinate.`).
- In-memory per-IP throttle `_pricing_seen_at` bounded at `_PRICING_THROTTLE_MAX = 2000` with O(n) eviction (`User_Activity.py:62-64, 237-240`).
- `payment_webhook` currently raises 501 (stub) — `User_Activity.py:310-330`. WM-02/WM-03 (signature + replay) target a not-yet-wired path. Spec must reflect.

### 2. Test-spec → code mapping

| Task | Spec citation | Verified file:line | Status |
|---|---|---|---|
| WM-01 failure-isolation | global exception handler | `website/app.py:197-209` (`_on_unhandled_exception` wraps `notify_app_error` in its own try/except) | OK |
| WM-02 inbound auth/validation | `POST /digitalocean`, `POST /payment` | `DO_Alerts.py:159-202` (auth via `alert_uuid`==`DO_ALERT_WEBHOOK_SECRET`); `User_Activity.py:310-330` (stub 501) | OK — but `/payment` returns 501; tests must assert stub contract, not signature flow |
| WM-03 HMAC + replay | DO + payment | **STALE** — DO uses plaintext `alert_uuid` body field, not HMAC + timestamp. No replay window exists. Payment handler is a 501 stub. | STALE in current code |
| WM-04 PII redaction `_mask_email` | `User_Activity._mask_email` | `User_Activity.py:155-162` | OK |
| WM-05 Slack 429 backoff | `post_to_*` | **STALE** — `post_to_app_errors` (`App_Errors.py:89-112`), `post_to_do_alerts` (`DO_Alerts.py:89-109`), `post_to_user_activity` (`User_Activity.py:110-132`) all do single POST + log, NO retry/backoff/circuit-breaker. | STALE — feature not implemented |
| WM-06 multi-channel isolation | per-channel `post_to_*` | OK — three independent `try/except` blocks | OK |
| WM-07 outbound rate-limit/queue | spec | **STALE** — no bounded queue. Calls scheduled via `asyncio.create_task` in `app.py:413-417`. | STALE |
| WM-08 fuzz `DOAlertPayload` + payment | `DOAlertPayload` | `DO_Alerts.py:130-141` (`model_config = {"extra": "allow"}`) | OK on DO; payment fuzz blocked by 501 stub |
| WM-09 `_severity` table-driven | `DO_Alerts._severity` | `DO_Alerts.py:144-151` (resolved/None/critical≥95/warning) | OK |
| WM-10 structured logging | all paths | `logger.warning/info/error/exception` calls present in all `post_to_*` — uses stdlib `logging`, NOT JSON structured logger. Spec assertion of "structured" should mean "named logger + level + message", not JSON. | PARTIAL — clarify scope |
| WM-11 canary heartbeat | (impl) | **NOT IMPLEMENTED** — no scheduled-task canary exists yet | STALE |
| WM-12 healthz contract smoke | 3 endpoints | 3 `*_healthz` endpoints all return `{ok, channel, webhook_configured}`; user-activity also returns `pricing_throttle_seen` | OK |
| WM-13 `notify_pricing_visit` non-blocking | scheduled call | `app.py:413-417` uses `asyncio.get_running_loop().create_task(...)` — fire-and-forget; spec assertion holds | OK |
| WM-14 env validation | boot-time | **STALE** — no boot-time check; `post_to_*` logs a warning at first call if env unset. | STALE |

**~50% stale or pointing at unimplemented features** — matches the project's "Research Before Recommend" expectation. Plan must reduce WM-03/WM-05/WM-07/WM-11/WM-14 to "spec-only/operator-decision" or add an implementation phase BEFORE writing the test.

### 3. Cross-wave dependencies

| Caller | Path | Wave touched | Mock status |
|---|---|---|---|
| `notify_app_error` ← global exception handler | `website/app.py:197-209` | WAVE-A (app shell) | No mock; tests use `respx`/`httpx_mock` on Slack URL |
| `notify_pricing_visit` ← pricing route | `website/app.py:413-417` | WAVE-A (pricing UI) | Same; pricing route handler imports lazily |
| `notify_new_signup` (documented call site) ← `core.profiles` insert path | `website/core/supabase_v2/repositories/core_repository.py` (per docstring) | WAVE-B (KG/v2 schemas) | `mint_test_user_with_workspaces` in `tests/v2/fixtures/users.py` already mocks profile insert path; need NEW signup-only fixture that hits the `INSERT` branch (not SELECT short-circuit) |
| `notify_payment` ← future provider webhook | not yet wired | WAVE-D self | n/a — covered by 501 stub assertion |

Established mocks reusable:
- `tests/integration/v2/conftest.py` — `asyncpg_pool`, `mint_user`, `pytest_sessionfinish` cleanup hook (per CLAUDE.md DB v2 closeout section).
- `tests/conftest.py` (project root, line ≥1 per mem-vault edit history) — sample URLs + `--live` flag.

No existing `respx` or `httpx_mock` fixture in repo (verify in Phase 1) — Phase 1 must add a `slack_webhook_mock` fixture covering all 3 `SLACK_WEBHOOK_*` env vars.

### 4. Known risks

| Risk | Code anchor | Mitigation surface |
|---|---|---|
| Slack 429 storm cascades into app pause | no backoff (`post_to_*` makes one POST per call, 10 s timeout) | WM-05 first needs implementation, then test |
| Unbounded `asyncio.create_task` from `notify_pricing_visit` under bot scan | `app.py:415` fire-and-forget | WM-07 needs bounded task pool |
| Payment webhook spoof — when wired | `User_Activity.py:310-330` stub explicitly warns | WM-02/WM-03 are pre-implementation guards |
| PII leak in `notify_new_signup` body | `User_Activity.py:204-205` body literally includes the masked email; `fields["email"]` also masked. Risk = future devs adding raw email/IP to other channels. | WM-04 should add a `grep`-style CI test forbidding raw email regex in payload builders |
| Memory pressure on 2 GB droplet — `_pricing_seen_at` bounded but `min(...)` eviction is O(n) every insert beyond cap | `User_Activity.py:237-240` | Stress test at n=2000 should be in WM-07 |
| `_client_ip` trusts unverified `X-Forwarded-For` and `cf-connecting-ip` | `User_Activity.py:140-152` | OK in current deploy (Cloudflare → Caddy → uvicorn) but document the trust boundary; flag as risk if reverse proxy changes |
| `DO_ALERT_WEBHOOK_SECRET` is shared-secret in payload body, not HMAC over body. Non-constant-time string compare via `!=`. | `DO_Alerts.py:170-173` | Phase 1 should switch to `hmac.compare_digest` (1-line surgical fix per WAVE protocol) |

### 5. Citations
- [OWASP Top 10:2021 A09 — Security Logging & Monitoring Failures (2021)](https://owasp.org/Top10/A09_2021-Security_Logging_and_Monitoring_Failures/)
- [Slack — Rate limits for incoming webhooks (2024)](https://api.slack.com/apis/rate-limits)
- [Slack — Handling rate limits / Retry-After (2024)](https://api.slack.com/docs/rate-limits#handling-rate-limit-errors)
- [Standard webhooks — Replay protection & timestamp window ≤5 min (2023)](https://www.standardwebhooks.com/#design)
- [SRE Book Ch. 22 — Addressing Cascading Failures (Google, 2017)](https://sre.google/sre-book/addressing-cascading-failures/)
- [OWASP API Security Top 10 — 2023 (API1 BOLA, API2 Broken Auth)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
- Code anchors: `website/features/web_monitor/App_Errors.py:89-149`, `DO_Alerts.py:130-202`, `User_Activity.py:110-261`, `website/app.py:197-209,413-417`

---

## Module: user_home (MODERATE tier, 8 tasks)

### 1. File inventory

| File | Lines | Surface |
|---|---|---|
| `website/features/user_home/index.html` | 341 | Welcome heading, `home-vault` panel (zettels), avatar header (duplicates `header.html` markup — see Risk #3 below), avatar dropdown, summary loader DOM, add-zettel form, avatar-picker modal, phone-collect modal not present in shipped HTML (see stale citation in §2) |
| `website/features/user_home/js/home.js` | 1411 | IIFE-wrapped. Top-level: `resolveDOM`, `setBodyScrollLocked`, `toSafeHttpUrl`, `init` (line 76), supabase boot, `/api/me` fetch, `/api/rag/sandboxes` fetch, Add Zettel facade call with `client_action_id`, `window.ZKPricing.openPurchase` quota-exhausted resume flow, `/api/graph?view=my` fetch (line 267 + 560), avatar-modal bind, kasten create form, sign-out, `init()` invoked at DOMContentLoaded (line 1407) AND immediately (line 1409). |
| `website/features/user_home/css/home.css` | (not opened) | Color/typography rules subject to UH-03 scan |
| Mounts | `app.py:234-235` | `/home/css/*`, `/home/js/*` static |
| Route | `app.py:361-364` (`@app.get("/home")`) | `if _is_mobile(request): RedirectResponse("/m/", 302); else _render_with_shell(HOME_DIR / "index.html")` |

### 2. Test-spec → code mapping

| Task | Spec citation | Verified file:line | Status |
|---|---|---|---|
| UH-01 signed-in smoke + redirect anonymous | spec mentions `redirect to landing if anonymous` | `home.js:108` (`window.location.href = '/'`). Server-side route at `app.py:361-364` ALWAYS renders the page for anonymous users — redirect happens client-side after Supabase session check fails. | PARTIAL — spec implies server-side redirect; reality is client-side. Test must drive a real browser (Playwright/Chrome). |
| UH-02 visual regression | (impl) | n/a — Playwright baseline file not yet present | OK to author |
| UH-03 no-purple color scan | spec — HSL 250-290 + `#A78BFA` | static grep + computed-style scan against `home.css` + inline `index.html` | OK |
| UH-04 mobile UA → `/m/` redirect | spec — Pixel + iPhone UA | regex `_MOBILE_RE` at `app.py:75-78`; route handler `app.py:344-348, 361-364` | OK — UA regex pattern verified |
| UH-05 phone-modal + pricing CTA | spec — "Phone-modal + pricing CTA round-trip" | **STALE** — `index.html` (341 lines) does NOT contain a phone-collect modal in the shipped markup. The pricing CTA path goes through `window.ZKPricing.openPurchase` (`home.js:846-851`). Test should pivot to "quota-exhausted resume flow" instead. | STALE |
| UH-06 axe-core WCAG 2.2 AA | spec | n/a — needs Playwright + `@axe-core/playwright` | OK to author |
| UH-07 shell composition regression | `_render_with_shell` | `app.py:53-68` reads `HEADER_DIR / "header.html"` (`features/header/header.html`) and FOOTER per request. Fall-through: missing fragment files = raw page (no error). | OK — test the fall-through case |
| UH-08 synthetic signed-in home monitor | (impl) | n/a — relies on `mcp__scheduled-tasks` | OK |

Stale citations: 1 of 8 confirmed stale (UH-05). Spec was authored before phone-collect modal was removed.

### 3. Cross-wave dependencies

| Endpoint called by `home.js` | Wave | Mock available? |
|---|---|---|
| `GET /api/auth/config` | WAVE-A (user_auth) | check `tests/unit/user_auth/test_auth_error_ux.py` patterns |
| `GET /api/me` | WAVE-A | reuse `mint_user` fixture from `tests/integration/v2/conftest.py` |
| `PUT /api/me/avatar` (via `window.ZKHeader.setAvatarById`) | header surface | covered by HD-* |
| `GET /api/rag/sandboxes` | WAVE-B (rag kasten) | `tests/integration/v2/` patterns exist |
| `POST /api/zettels/add` | WAVE-C (summarization_engine) | dispatched via `mock_gemini_pool` from WAVE-C plan |
| `GET /api/graph?view=my` | WAVE-C (knowledge_graph) | `mock_supabase_kg_v2` per WAVE-C plan |
| `POST /api/rag/sandboxes` (kasten create) | WAVE-B | quota-exhausted `code:quota_exhausted` path → `window.ZKPricing.openPurchase` round-trip |
| Pricing surface `window.ZKPricing.openPurchase` | WAVE-A footer/pricing | confirm `ZKPricing` exposes a Playwright-stubbable seam |

Mocks established:
- `tests/v2/fixtures/users.py::mint_test_user_with_workspaces` — already includes `email`. Reusable for UH-01.
- No browser/Playwright fixture exists yet in repo (`find conftest.py` returned 4 — all backend). Phase 1 must add `tests/integration/browser/conftest.py` with a fresh-context fixture per `Playwright Visual Comparisons` doc.

### 4. Known risks

| Risk | Anchor | Notes |
|---|---|---|
| Double `init()` invocation (line 1407 AND 1409) creates race: DOMContentLoaded handler runs again after immediate call resolves async work. | `home.js:1405-1410` | Concurrency test — guard against duplicate `/api/me` fetches |
| Header avatar markup duplicated in `index.html:27-62` vs `header.html` — page renders BOTH if a future commit injects header via `_render_with_shell`. Spec says no `<!--ZK_HEADER-->` placeholder in `/home`'s `index.html` — verify before UH-07. | `home/index.html:18-63` | UH-07 must lock current behaviour (single header) AND assert placeholder absence |
| `toSafeHttpUrl` is the only client-side SSRF guard before Add Zettel submission; relies on `URL()` constructor. Server-side `validate_url()` is the canonical check. | `home.js:60-72` | Cross-wave: WAVE-C already covers SSRF (`SE-01`). |
| Color-rule violation surface: 4 inline `<svg>` blocks in `index.html` use `stroke="currentColor"`; computed-style scan must run with logged-in DOM, not raw HTML | `index.html:34,40,46,56` | UH-03 must use Playwright `evaluate()` to grab `getComputedStyle` |
| `client_action_id` for idempotency uses `Date.now()+Math.random().toString(36).slice(2)` — collisions possible at 1k+ users/sec but irrelevant at current scale; flag for 10k+ ramp | `home.js:719, 1233` | Document in Phase 1 plan |
| BFLA: `/api/graph?view=my` (`home.js:267, 560`) — must assert server denies `view=my` with another user's session. WAVE-C `KG-11`. | n/a | Already in WAVE-C scope |

### 5. Citations
- [Playwright — Visual Comparisons / `toHaveScreenshot` (2024)](https://playwright.dev/docs/test-snapshots)
- [Playwright — Accessibility Testing (axe-core integration, 2024)](https://playwright.dev/docs/accessibility-testing)
- [WCAG 2.2 — W3C Recommendation (2023)](https://www.w3.org/TR/WCAG22/)
- [OWASP API Security Top 10 — 2023 (API1 BOLA, API2 Broken Auth)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
- [OWASP ASVS v5 §V14.4 — Mobile UA detection caveats (2024)](https://owasp.org/www-project-application-security-verification-standard/)
- Code anchors: `website/app.py:53-78, 344-364, 361-364`, `website/features/user_home/index.html:1-80`, `website/features/user_home/js/home.js:60-110, 791-851, 1405-1410`

---

## Module: header (MODERATE tier, 6 tasks)

### 1. File inventory

| File | Lines | Surface |
|---|---|---|
| `website/features/header/header.html` | 72 | Single-fragment `<header class="header zk-header">` — back button (`data-zk-back`), branding + tagline, avatar wrap with dropdown menu (5 links: Dashboard, Zettels, Kastens, Nexus, KG + Sign out). No `<script>`/`<style>` tags — pure markup. |
| `website/features/header/js/header.js` | 296 | IIFE. Exposes `window.ZKHeader = { boot, setAvatarById, onSignOut, _internal }` (line 230). Avatar resolution via `/api/me`, localStorage cache key `zk-avatar-url-<profileId>`, 60-avatar SVG pool at `/artifacts/avatars/avatar_N.svg`. `initBasics` (back button + dropdown) auto-runs at DOMContentLoaded (line 223-226). |
| `website/features/header/css/header.css` | (not opened) | Color scope for HD-03 |
| Mounts | `app.py:295-296` | `/header/css/*`, `/header/js/*` |
| Injection | `app.py:53-68` (`_render_with_shell`) — replaces `<!--ZK_HEADER-->` placeholder | Every desktop page rendered through this path |

### 2. Test-spec → code mapping

| Task | Spec citation | Verified | Status |
|---|---|---|---|
| HD-01 shell-injection across 7 inner pages | spec: "7 inner pages mounted in `app.py`" | Routes with `_render_with_shell`: `/` (`app.py:344-348`), `/knowledge-graph` (350-354), `/home` (361-364), `/home/nexus` (367-374), `/home/zettels` (376-380), `/home/kastens` (382-386), `/home/rag` (388-392), `/about` (398-402), `/pricing` (404-418). **That is 9 routes, not 7.** | STALE count — update to 9 |
| HD-02 XSS dynamic fields | spec: "dynamic fields injected by `_render_with_shell` escaped" | `_render_with_shell` (`app.py:53-68`) does **`html.replace(placeholder, header_html)`** — NO escaping of the header fragment itself (it's trusted static content). The dynamic-field risk is for variables interpolated INTO the header. Current `header.html` has no Jinja/f-string interpolation — it's static. | PARTIALLY STALE — there are no dynamic fields today. HD-02 should reframe as "verify `header.html` contains no `{{...}}` or `${...}` placeholders that could be tainted" + a regression guard |
| HD-03 color rule (no amber outside `/knowledge-graph`, no purple anywhere) | static + computed-style scan | needs `header.css` open + Playwright computed-style scan | OK |
| HD-04 visual regression at 3 widths | (impl) | n/a — baseline | OK to author |
| HD-05 JS-collision on Kastens/RAG/Zettels | spec | `header.js` mutates DOM via `getElementById('avatar-btn')`, `getElementById('avatar-dropdown')`, `getElementById('menu-signout')`. Same IDs appear in `user_home/index.html:30,37,54` (duplicated avatar markup) — **double-bind risk confirmed**. | OK — collision test will pass on /home and possibly fail on inner pages depending on duplication |
| HD-06 asset-integrity post-deploy | `/header/js/header.js` 200 | static mount `app.py:295-296` | OK |

### 3. Cross-wave dependencies

| Surface | Wave | Mock status |
|---|---|---|
| `/api/me` (`header.js:185-192`) | WAVE-A user_auth | reuse `mint_user` |
| `PUT /api/me/avatar` (`header.js:273-277`) | WAVE-A user_auth | needs route mock |
| Avatar SVGs at `/artifacts/avatars/avatar_N.svg` | static mount `app.py:299` | reuse `_mount_static_if_exists` test |
| `_render_with_shell` (consumes `header.html`) | shell surface — touches every wave's UI | UH-07 in user_home wave covers fall-through; HD-01 covers happy path |

No existing fixture provides a logged-in Playwright session against the dev FastAPI app. Phase 1 must add a shared `authed_browser` fixture (used by both UH-* and HD-*).

### 4. Known risks

| Risk | Anchor | Notes |
|---|---|---|
| **Duplicate DOM IDs** — `home/index.html` ships its own copy of `#avatar-btn` / `#avatar-dropdown` / `#menu-signout` (lines 30, 37, 54), while `header.html` also declares the same IDs (lines 20, 28, 63). On a page that injects header via `_render_with_shell`, the page DOM ends up with **duplicates** ⇒ `getElementById` returns the FIRST one ⇒ events bind to that ⇒ silent UX degradation. HD-05 will catch this. | `header.html:20,28,63` vs `home/index.html:30,37,54` | P1 fix recommendation: switch one set to scoped class or namespaced IDs |
| `header.js` auto-runs `initBasics` at DOMContentLoaded (line 223-226) AND `window.ZKHeader.boot` is called by every consuming page → potential double-bind on dropdown if `boot()` re-resolves refs. `bindAvatarDropdown` uses `addEventListener` without dedup guard. | `header.js:203-215, 223-226` | Concurrency / re-init test |
| Avatar URL cache trust — `readCached(profileId)` returns whatever localStorage holds; if a profile's `avatar_url` server-side ever points off-domain, the `AVATAR_PATH_RE = /\/artifacts\/avatars\/avatar_\d+\.svg/` regex (`header.js:17`) is the only validation gate. Confirm any `serverUrl` not matching this regex is rejected. | `header.js:113-141` | Inspect `idFromUrl` + `resolveAvatarUrl` rejection branch in HD-02 reframe |
| Asset 404 silent fail — `preload(url, timeoutMs)` swallows errors and falls back to a random avatar (`header.js:144-179`); a missing `/artifacts/avatars/avatar_N.svg` deploys silently. HD-06 must assert all 60 SVGs (0-59) return 200. | `header.js:144-179, 16` | Scale: 60 HEAD requests in HD-06 |
| Accessibility — back button has `aria-label="Go back"` ✓ (`header.html:3`). Avatar button has `aria-label`, `aria-haspopup`, `aria-expanded` ✓ (`home/index.html:30` AND `header.html:20`) — but the static fragment in `header.html` is **missing `aria-expanded` and `aria-haspopup`** (line 20 has only `title` + `aria-label`). | `header.html:20` | HD-04 / WCAG 2.2 — flag for a 1-line fix |
| BFLA on `/api/me/avatar` — `setAvatarById` PUTs `avatar_id` with the current user's bearer; server must enforce `auth.uid()` ownership. | `header.js:273-277` | Cross-cuts WAVE-A; verify a denial test exists |

### 5. Citations
- [OWASP XSS Prevention Cheat Sheet (2024)](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [Playwright — Visual Comparisons (2024)](https://playwright.dev/docs/test-snapshots)
- [OWASP WSTG v4.2 — Content Security Policy (2023)](https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/12-Test_for_Content_Security_Policy)
- [WAI-ARIA 1.2 — `aria-haspopup` / `aria-expanded` on menu buttons (W3C, 2023)](https://www.w3.org/TR/wai-aria-1.2/#menubutton)
- [WCAG 2.2 Success Criterion 4.1.2 — Name, Role, Value (2023)](https://www.w3.org/TR/WCAG22/#name-role-value)
- Code anchors: `website/features/header/header.html:1-72`, `website/features/header/js/header.js:13-296`, `website/app.py:53-68, 295-296, 344-418`

---

## Cross-module: plan amendments required

1. **WM-03/WM-05/WM-07/WM-11/WM-14** target features that do not exist in current code. Operator decision needed: (a) author tests against spec'd behavior + implement in Phase 1 (recommended for WM-05 backoff + WM-14 env-validation — both are surgical), or (b) downgrade those tasks to "design spec" status.
2. **WM-03 surgical fix**: `DO_Alerts.py:170-173` uses `!=` for shared-secret compare — swap to `hmac.compare_digest` in Phase 1 (1-line fix; matches WAVE protocol "fix inline if surgical ≤30 lines").
3. **UH-05** (phone modal) is stale — pivot to quota-exhausted resume flow which is actually shipped at `home.js:846-851`.
4. **HD-01** mounted-page count is wrong — spec says 7, reality is 9 (`/`, `/knowledge-graph`, `/home`, `/home/nexus`, `/home/zettels`, `/home/kastens`, `/home/rag`, `/about`, `/pricing`).
5. **HD-02** has no taint surface today — reframe as a regression guard ("`header.html` MUST NOT introduce templated fields without explicit escaping").
6. **HD-05 collision test** is the highest-value cheap win — the duplicate `#avatar-btn` ID is a real latent bug.
7. **New shared fixture (Phase 1)**: `authed_browser` (Playwright + Supabase session minted via `mint_test_user_with_workspaces`) — required by all UH-* and HD-* visual/interaction tasks. No precedent in `tests/`.
8. **New shared fixture (Phase 1)**: `slack_webhook_mock` (`respx` patching all 3 `SLACK_WEBHOOK_*` env URLs) — required by every WM-* test.

## Operator decisions needed (P0)

- **DECISION D-1**: WM-03/WM-05/WM-07/WM-11/WM-14 — implement-then-test (Phase 1 adds the feature) OR downgrade-to-spec? P1-recommended: implement backoff (WM-05) + env validation (WM-14) + HMAC swap (WM-03 surgical); defer WM-07 + WM-11 to a hardening pass.
- **DECISION D-2**: Duplicate `#avatar-btn` / `#avatar-dropdown` / `#menu-signout` IDs between `home/index.html` and `header.html` — fix the IDs in Phase 1 (small DOM surgery) OR add a Phase 1 test that pins current behavior + open a follow-up?
- **DECISION D-3**: Phone-collect modal — spec UH-05 obsolete. Drop, or re-introduce the modal as a planned feature?
- **DECISION D-4**: `header.html:20` avatar button missing `aria-haspopup`/`aria-expanded` — 2-line fix in Phase 1, or treat as a separate accessibility hardening task?
- **DECISION D-5**: Playwright browser tests — adopt `@axe-core/playwright` + `Claude_in_Chrome` MCP (spec UH-06/HD-01) for in-CI runs, or restrict to `--live` staging only?

## Phase 1 readiness

**CAN dispatch immediately**:
- web_monitor WM-01 (failure isolation), WM-04 (mask_email), WM-09 (`_severity`), WM-10 (logging contract), WM-12 (healthz smoke), WM-13 (non-blocking pricing).
- user_home UH-01, UH-03, UH-04, UH-07 (excluding stale UH-05 piece).
- header HD-01 (9 pages, not 7), HD-03 (color scan), HD-05 (JS collision — high-value), HD-06 (asset 60-SVG sweep).

**NEEDS DECISION GATE**:
- WM-02/WM-03/WM-05/WM-07/WM-11/WM-14 — pending D-1.
- UH-05 — pending D-3.
- HD-02 (reframe) and HD-04/UH-02/UH-06 (Playwright infra) — pending D-4 and D-5.

## New fixture requirements

- `slack_webhook_mock` — `respx` patching `SLACK_WEBHOOK_APP_ERRORS`, `SLACK_WEBHOOK_DO_ALERT`, `SLACK_WEBHOOK_USER_ACTIVITY`; supports forced 200/429/500 + Retry-After header injection. Required by every WM-* test.
- `authed_browser` — Playwright fresh context with a Supabase session minted by `mint_test_user_with_workspaces`. Required by UH-01/02/06/07 and HD-01/04/05.
- `static_color_scan` — regex-based grep helper for HD-03 / UH-03 forbidding HSL 250-290 + `#A78BFA` + `#7C3AED` + `purple|violet|lavender` outside `/knowledge-graph` CSS scope.
- `mint_signup_only_user` — wraps `mint_test_user_with_workspaces` and forces the `INSERT` branch (not SELECT), so `notify_new_signup` fires. New.
- `frozen_clock` (reuse from WAVE-C) — for WM-13 throttle window + WM-05 backoff jitter tests.
- `respx_session` (reuse if exists) — verify or add.
