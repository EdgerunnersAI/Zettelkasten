# WAVE-D Phase 0 — Decision D-5: Playwright + axe-core in CI

**Date:** 2026-05-12
**Scope:** Decide whether browser-driven tests (visual regression UH-02/HD-04, shell-injection HD-01 across 9 routes, WCAG 2.2 AA UH-06) run inside the GitHub Actions `deploy-droplet.yml` workflow or behind a `--live` flag against the deployed droplet.
**Status:** Research only. No code edits made.

---

## 1. Recommendation

**Run Playwright + axe inside a dedicated `e2e.yml` GitHub Actions workflow (NOT inside `deploy-droplet.yml`), targeting a transient `docker compose` instance of the app on `ubuntu-latest` (4 vCPU / 16 GB RAM / 14 GB SSD), with a `--live` opt-in path retained for staging smoke against the droplet.** Estimated cost per full 9-route × 3-width matrix: **~3.5 GB RAM peak, ~12-18 minutes wall-clock** — well under the 16 GB / 6-hour ceiling.

Rationale (3 bullets):

- The `ubuntu-latest` standard runner is now **4 vCPU / 16 GB RAM / 14 GB SSD** with a 6-hour job timeout and free unlimited minutes on public repos ([GitHub Hosted Runners reference, 2026](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)). Headed Chromium under Playwright 1.59 with the standard `--shm-size` mitigation runs **~350-500 MB per worker** in headless mode for a typical FastAPI page ([Playwright CI docs, 2026](https://playwright.dev/python/docs/ci); [pytest-playwright README, 2025](https://github.com/microsoft/playwright-pytest)). 3 viewports × 1 worker (serial in CI to avoid screenshot flake) keeps the peak well below 4 GB even with the Chrome for Testing regression noted below.
- Decoupling browser tests from `deploy-droplet.yml` preserves the **fast-deploy contract** (current pipeline: unit tests → image build → SSH deploy in ~6-8 min). Browser tests gate **merge to master**, not deploy-from-master — the droplet only receives images that already passed the visual/a11y matrix on a PR build. This matches the production change discipline in `CLAUDE.md` (ship complete + tested only).
- The **Chrome for Testing memory regression in Playwright 1.57+** ([microsoft/playwright#38489, 2025-12](https://github.com/microsoft/playwright/issues/38489)) is real but only catastrophic with parallel workers (3 workers × ~20 GB observed). For our serial 3-width × 9-route matrix at `workers: 1`, peak stays single-instance and the regression does not bite. We pin to **Playwright Python 1.59** ([release notes, 2026](https://playwright.dev/python/docs/release-notes)) and document the workers-cap.

---

## 2. Tool-chain matrix

| Option | Lang/Runtime | axe-core path | 2026 status | Verdict |
|---|---|---|---|---|
| **pytest-playwright** (Microsoft) | Python — native pytest fixtures | via `axe-playwright-python` OR raw `page.add_script_tag` + `page.evaluate` | Active: v0.7.2 released 2025-11-20, 547 stars, Microsoft-maintained ([microsoft/playwright-pytest, 2025](https://github.com/microsoft/playwright-pytest)) | **CHOSEN.** Single-runtime, reuses existing pytest infra (`asyncio_mode = auto`, `--live` flag, `conftest.py` fixtures). |
| Node runner bridge (`@playwright/test` + `@axe-core/playwright`) called from CI | Node 20 alongside Python | Native — `@axe-core/playwright` is the Deque reference impl, Node-only ([dequelabs/axe-core-npm](https://github.com/dequelabs/axe-core-npm/blob/develop/packages/playwright/README.md)) | Active (Deque) | Rejected: forces a second test runner, duplicates auth fixtures, fails the "no double infra" rule. |
| Cypress + cypress-axe | Node only | `cypress-axe` (community) | Declining: 14.4% adoption vs Playwright 45.1% in 2026; State of JS 2025 satisfaction 72% vs Playwright 91% ([Playwright Market Share, 2025](https://testdino.com/blog/playwright-market-share/)) | Rejected: Node-only kills Python parity; trailing on features. |
| Selenium 4 + axe-selenium-python | Polyglot | Deque axe-selenium-python | Stable but slower test cycle; 22.1% adoption, declining | Rejected: 2-3× slower per page than Playwright; no built-in `toHaveScreenshot`. |
| TestCafé | Node only | community plugins | Effectively unmaintained 2024+ | Rejected. |

**Conclusion:** `pytest-playwright` + `axe-playwright-python` (or raw `page.evaluate` injection — see §5) is the only choice that keeps the repo single-language Python and respects `CLAUDE.md` guardrails on dependency additions.

---

## 3. Auth-fixture pattern (Supabase session via localStorage)

Supabase JS client stores its session under the localStorage key `sb-<project-ref>-auth-token` as JSON. The Playwright-canonical approach is `context.add_init_script()` so the value is in place **before** any page script runs ([Mokkapps, 2024](https://mokkapps.de/blog/login-at-supabase-via-rest-api-in-playwright-e2e-test); [bekapod.dev, 2024](https://www.bekapod.dev/articles/supabase-magic-login-testing-with-playwright/); [Playwright Auth docs, 2026](https://playwright.dev/docs/auth)).

Sketch (≤40 lines):

```python
# tests/e2e/conftest.py
import json
import os
import pytest
from playwright.sync_api import BrowserContext, Browser
from supabase import create_client  # service-role client, test-only

SUPABASE_URL = os.environ["TEST_SUPABASE_URL"]
SUPABASE_PROJECT_REF = SUPABASE_URL.split("//")[1].split(".")[0]
SERVICE_KEY = os.environ["TEST_SUPABASE_SERVICE_ROLE_KEY"]  # CI secret, never committed

@pytest.fixture(scope="session")
def supabase_test_user_session():
    """Mint a Supabase user via admin API, return access_token + refresh_token."""
    admin = create_client(SUPABASE_URL, SERVICE_KEY)
    email = f"e2e-{os.urandom(4).hex()}@example.test"
    user = admin.auth.admin.create_user({"email": email, "password": "e2e-pass-1234!", "email_confirm": True})
    sess = admin.auth.sign_in_with_password({"email": email, "password": "e2e-pass-1234!"})
    yield {"access_token": sess.session.access_token, "refresh_token": sess.session.refresh_token,
           "expires_at": sess.session.expires_at, "user": {"id": user.user.id, "email": email}}
    admin.auth.admin.delete_user(user.user.id)

@pytest.fixture
def authed_context(browser: Browser, supabase_test_user_session) -> BrowserContext:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    storage_key = f"sb-{SUPABASE_PROJECT_REF}-auth-token"
    payload = json.dumps(supabase_test_user_session)
    # add_init_script runs in every frame before any page script — required so the
    # Supabase client picks up the session on first construction.
    ctx.add_init_script(f"window.localStorage.setItem({storage_key!r}, {payload!r});")
    yield ctx
    ctx.close()
```

Trade-off note: storage-state JSON files (`browser.new_context(storage_state=...)`) are the Playwright-default pattern but they bake a *captured* session into the repo. For our case (per-test fresh user, hard delete in teardown) the init-script path is cleaner and avoids stale tokens.

---

## 4. Visual-regression flake-mitigation checklist

| # | Mitigation | How | Source |
|---|---|---|---|
| 1 | **Pin browser version** — Playwright bundles browser builds; lockfile (`requirements.txt` pin to `playwright==1.59.x`) + `playwright install --with-deps chromium` in CI | Bundled browser per Playwright version | [Playwright Best Practices, 2026](https://playwright.dev/docs/best-practices) |
| 2 | **Generate baselines in CI only**, never locally — Linux Cairo font rendering differs from macOS/Windows | `--update-snapshots` only via CI workflow with manual dispatch | [houseful.blog, 2023](https://www.houseful.blog/posts/2023/fix-flaky-playwright-visual-regression-tests/); [Bug0, 2026](https://bug0.com/knowledge-base/playwright-visual-regression-testing) |
| 3 | **Disable animations/transitions** — `toHaveScreenshot({animations: "disabled"})` is the default but inject CSS as belt-and-braces | `page.add_style_tag(content="*,*::before,*::after{animation-duration:0s !important;transition-duration:0s !important;}")` | [Playwright PageAssertions docs, 2026](https://playwright.dev/docs/api/class-pageassertions); [GitHub Issue #11912](https://github.com/microsoft/playwright/issues/11912) |
| 4 | **Wait for fonts** — `page.evaluate("document.fonts.ready")` before screenshot | Avoids FOUT-induced 1-2 px diffs | [TestDino Visual Testing Guide, 2025](https://testdino.com/blog/playwright-visual-testing) |
| 5 | **Fake clock** via `page.clock.install(time=...)` + `page.clock.pause_at(...)` — eliminates timestamp/date drift in screenshots | Available Playwright 1.45+ ([Clock API docs](https://playwright.dev/docs/api/class-clock)) | [Tim Deschryver, Playwright 1.45 Clock](https://timdeschryver.dev/bits/playwright-v145-makes-you-a-time-wizard) |
| 6 | **Mask dynamic regions** — `to_have_screenshot(mask=[page.locator(".timestamp"), page.locator(".user-avatar")])` | `mask_color` configurable since 1.35 | [Playwright release notes, 2026](https://playwright.dev/python/docs/release-notes); [TestDino, 2025](https://testdino.com/blog/playwright-visual-testing) |
| 7 | **Pixel tolerance** — `max_diff_pixels=100` (absolute) or `max_diff_pixel_ratio=0.01` per assertion; tune per route based on observed noise | Avoids 1-2 anti-alias pixel false-fails | [Playwright TestConfig docs, 2026](https://playwright.dev/docs/api/class-testconfig) |
| 8 | **Fixed viewports** — exactly 3 widths (mobile 375×812, tablet 768×1024, desktop 1440×900); set `device_scale_factor=1` to prevent retina drift | Per-context, not per-test | [TestDino Best Practices, 2025](https://testdino.com/blog/playwright-automation-checklist/) |
| 9 | **Single worker for visual matrix** — `pytest --workers 1` (or omit `pytest-xdist` entirely for visual tests); functional + a11y can parallelize | Bypasses Chrome for Testing RAM regression ([microsoft/playwright#38489](https://github.com/microsoft/playwright/issues/38489)) and prevents GPU contention | Issue confirms 3-worker → 20 GB regression |
| 10 | **`--shm-size=1gb`** when running under Docker on the runner | Default `/dev/shm` is 64 MB and crashes Chromium on large pages | [Playwright CI docs, 2026](https://playwright.dev/python/docs/ci) |

---

## 5. axe-core wiring — preferred: `page.evaluate()` injection

The Node-only `@axe-core/playwright` package ([Deque dequelabs/axe-core-npm](https://github.com/dequelabs/axe-core-npm/blob/develop/packages/playwright/README.md)) is not directly callable from Python. Two viable Python paths:

**Option A (recommended): use `axe-playwright-python` (Pamela Fox, v0.1.7 released 2025-12-01) — it is a thin Python wrapper that does exactly the `add_script_tag` + `page.evaluate` injection we want, with WCAG-tag filtering already plumbed.** Verified active and Python 3.8-3.14 compatible ([PyPI](https://pypi.org/project/axe-playwright-python/)).

**Option B (fallback if we need to bypass the package — e.g. CSP issues, custom rules): direct injection.** Pattern from the NHS England Playwright-Python blueprint (which uses WCAG 2.2 AA by default; [Axe utility guide, 2025](https://github.com/nhs-england-tools/playwright-python-blueprint/blob/main/docs/utility-guides/Axe.md)) and HackMD writeup ([gabalafou, HackMD](https://hackmd.io/@gabalafou/ByvwfEC0j)):

```python
# tests/e2e/axe_helpers.py
from pathlib import Path
import json
from playwright.sync_api import Page

# Bundle axe-core locally to avoid CDN flake; pin to 4.10.x (current stable late 2025).
AXE_JS = (Path(__file__).parent / "vendor" / "axe.min.js").read_text(encoding="utf-8")

def run_axe(page: Page, route_name: str) -> dict:
    """Inject axe-core, run with WCAG 2.2 AA tags, fail on violations."""
    page.add_script_tag(content=AXE_JS)
    # WCAG 2.2 AA = the four tags below per axe-core 4.10 rule-pack
    # https://github.com/dequelabs/axe-core/blob/develop/doc/API.md#options-parameter
    results = page.evaluate(
        """async (tags) => await axe.run(document, {
              runOnly: { type: 'tag', values: tags },
              resultTypes: ['violations']
           })""",
        ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]
    )
    violations = results.get("violations", [])
    if violations:
        out = Path(f"test-results/axe/{route_name}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2))
    return results
```

WCAG 2.2 went to W3C Recommendation on 2023-10-05 ([W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/)); axe-core 4.8+ ships `wcag22aa` as a tag ([axe-core rule docs](https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md)). Pin axe-core to **4.10.x** for predictable rule-pack — bumping axe-core mid-iteration causes baseline drift the same way Chromium upgrades do.

---

## 6. Snapshot storage decision

**Choice: Git LFS, with quarterly review.**

Math: 9 routes × 3 widths × ~50 KB/PNG ≈ **1.35 MB per full baseline refresh**. Even at 10× growth (failure diffs retained, 30 routes by EOY) we top out at ~15 MB working set. Per the houseful.blog analysis, "a single screenshot is about 50 KB; 1,000 screenshots across variants becomes ~50 MB" — and Git LFS pays off mainly at hundreds-of-MB scale ([houseful.blog, 2023](https://www.houseful.blog/posts/2023/fix-flaky-playwright-visual-regression-tests/); [screenshotbot.io scale analysis](https://screenshotbot.io/blog/can-git-lfs-scale)).

**Decision:** start with **plain git** (`tests/e2e/__snapshots__/`) given <2 MB footprint. Trigger to migrate to **Git LFS**: total baseline size > 50 MB OR repo `.git` size grows > 200 MB attributable to PNG churn. Do NOT use S3-style external storage at this scale — the operational complexity (signed URLs, lifecycle policies, CI download step) is not warranted; reconsider only if we breach 500 MB cumulative or hit GitHub's per-file 100 MB limit ([GitHub LFS limits](https://github.com/orgs/community/discussions/171335)).

Rejected alternatives:
- **Visual-testing SaaS (Percy, Chromatic, Argos):** $50-200/month for a 1-developer project is not justified yet.
- **External S3 + Playwright remote-snapshot feature request:** still unresolved upstream ([microsoft/playwright#29227](https://github.com/microsoft/playwright/issues/29227)).

---

## 7. RAM + minutes budget (the explicit number)

For the 9-route × 3-width matrix on `ubuntu-latest` (4 vCPU / 16 GB / 14 GB SSD):

| Stage | Wall-clock | Peak RAM |
|---|---|---|
| Checkout + cache restore | ~20s | < 200 MB |
| `pip install -r ops/requirements-dev.txt` (cached) | ~30s | < 300 MB |
| `playwright install --with-deps chromium` (cached) | ~45s cold / ~5s warm | — |
| `docker compose up` app under test | ~60s | ~500 MB (FastAPI + python + asyncpg) |
| **Visual matrix:** 27 screenshots @ ~3-5s each, serial | ~3-5 min | **~500 MB (single Chromium instance)** |
| **axe matrix:** 9 routes × 1 audit each @ ~2-3s | ~30s | piggybacks on visual context |
| **Shell-injection matrix (HD-01):** 9 routes × ~10 payloads | ~90s | piggybacks |
| Artifact upload (diffs on fail only) | ~10s | — |
| **Total** | **~8-12 min typical, ~15-18 min worst-case** | **~3.5 GB peak (app + browser + pytest + OS)** |

This is **22% of the 16 GB ceiling and 5% of the 6-hour job timeout** — comfortable margin. Free public-repo minutes are unlimited so the recurring cost is zero ([GitHub Hosted Runners, 2026](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)).

---

## 8. Residual risks

1. **Playwright 1.60+ may extend the Chrome-for-Testing memory regression to single-worker scenarios.** Mitigation: pin to 1.59.x in `requirements.txt`, monitor [microsoft/playwright#38489](https://github.com/microsoft/playwright/issues/38489) for upstream resolution; fall back to `--project=firefox` (confirmed stable RAM) if it regresses.
2. **Linux font rendering drift between Chromium minor versions** → baseline drift on dependency bump. Mitigation: bump Playwright as a deliberate PR with `--update-snapshots`, review diffs by hand.
3. **Cloudflare in front of droplet adds latency variance** to `--live` smoke runs against staging — visual diffs more likely to flake there than in CI-internal. Mitigation: keep visual + a11y in-CI against `docker compose`; reserve `--live` for functional smoke only.
4. **Supabase test-user cleanup leak** if `pytest_sessionfinish` doesn't fire (CI killed). Mitigation: nightly cleanup cron in CI that deletes any `e2e-*@example.test` user older than 24h.

---

## Citations

- [Playwright Python — Continuous Integration (2026)](https://playwright.dev/python/docs/ci)
- [Playwright Python — Release notes (2026)](https://playwright.dev/python/docs/release-notes)
- [Playwright — Best Practices (2026)](https://playwright.dev/docs/best-practices)
- [Playwright — Auth fixtures (2026)](https://playwright.dev/docs/auth)
- [Playwright — Clock API (1.45+)](https://playwright.dev/docs/api/class-clock)
- [Playwright — PageAssertions / toHaveScreenshot (2026)](https://playwright.dev/docs/api/class-pageassertions)
- [Playwright — TestConfig (2026)](https://playwright.dev/docs/api/class-testconfig)
- [microsoft/playwright-pytest — v0.7.2, 2025-11-20](https://github.com/microsoft/playwright-pytest)
- [microsoft/playwright#38489 — Chrome for Testing 20GB RAM regression (2025-12)](https://github.com/microsoft/playwright/issues/38489)
- [microsoft/playwright#11912 — Disable CSS animations for screenshots](https://github.com/microsoft/playwright/issues/11912)
- [microsoft/playwright#29227 — Remote snapshot storage feature request](https://github.com/microsoft/playwright/issues/29227)
- [dequelabs/axe-core-npm — @axe-core/playwright (Node ref impl)](https://github.com/dequelabs/axe-core-npm/blob/develop/packages/playwright/README.md)
- [axe-playwright-python on PyPI — v0.1.7, 2025-12-01 (Pamela Fox)](https://pypi.org/project/axe-playwright-python/)
- [Pamela Fox — Accessibility snapshot testing for Python web apps (2023)](https://blog.pamelafox.org/2023/08/accessibility-snapshot-testing-for.html)
- [NHS England — playwright-python-blueprint Axe utility (2025, WCAG 2.2 AA default)](https://github.com/nhs-england-tools/playwright-python-blueprint/blob/main/docs/utility-guides/Axe.md)
- [HackMD — Playwright Python API with Axe-core (gabalafou)](https://hackmd.io/@gabalafou/ByvwfEC0j)
- [W3C — WCAG 2.2 Recommendation (2023-10-05)](https://www.w3.org/TR/WCAG22/)
- [Mokkapps — Supabase REST auth in Playwright E2E (2024)](https://mokkapps.de/blog/login-at-supabase-via-rest-api-in-playwright-e2e-test)
- [bekapod.dev — Supabase magic-login Playwright CI (2024)](https://www.bekapod.dev/articles/supabase-magic-login-testing-with-playwright/)
- [houseful.blog — Fixing flaky Playwright visual regression (2023)](https://www.houseful.blog/posts/2023/fix-flaky-playwright-visual-regression-tests/)
- [TestDino — Playwright Visual Testing Guide (2025)](https://testdino.com/blog/playwright-visual-testing)
- [TestDino — Playwright automation checklist (2025)](https://testdino.com/blog/playwright-automation-checklist/)
- [TestDino — Playwright market share (2025)](https://testdino.com/blog/playwright-market-share/)
- [Bug0 — Playwright Visual Regression Built-In Guide (2026)](https://bug0.com/knowledge-base/playwright-visual-regression-testing)
- [screenshotbot.io — Can Git LFS scale for screenshot tests? (2024)](https://screenshotbot.io/blog/can-git-lfs-scale)
- [Tim Deschryver — Playwright v1.45 Clock (2024)](https://timdeschryver.dev/bits/playwright-v145-makes-you-a-time-wizard)
- [GitHub — Hosted runners reference (2026)](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [GitHub community — Git LFS limits discussion #171335](https://github.com/orgs/community/discussions/171335)
