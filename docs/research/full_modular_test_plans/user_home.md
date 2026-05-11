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
| UH-05 | P2 | Phone-modal + pricing CTA round-trip (cached billing-profile path per recent perf commits) |
| UH-06 | P2 | axe-core WCAG 2.2 AA scan |
| UH-07 | P2 | Shell composition regression — broken header/footer must not hide auth regression |
| UH-08 | P3 | Synthetic signed-in home monitor |

## Execution order
UH-01 → UH-03 → UH-04 → UH-02 → UH-07 → UH-05 → UH-06 → UH-08

## Industry standards (≤5y)
- Playwright Visual Comparisons
- Playwright Accessibility Testing
- OWASP API1:2023 / API2:2023

## Live-test policy
Mocked auth in CI. `--live` staging. Production read-only `GET /home` unauthenticated (verifies redirect-to-landing).
