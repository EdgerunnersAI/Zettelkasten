# Test Plan — `header`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`header`.
Module path: `website/features/header/`.
Risk tier: **Moderate** (shared shell — wide blast radius).

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| Fragment render + shell injection | `header.html` via `_render_with_shell` |
| Nav highlight per route | `js/header.js` |
| Auth-state-aware sign-in/out button | `js/header.js` |
| Static asset mount `/header/*` | static handler |

## Tasks

| ID | P | Task |
|---|---|---|
| HD-01 | P1 | Shell-injection smoke across all **9** mounted desktop routes (Phase-0 corrected from "7 inner pages"): `/`, `/knowledge-graph`, `/home`, `/home/nexus`, `/home/zettels`, `/home/kastens`, `/home/rag`, `/about`, `/pricing`. 6 inject the shell fragment today (`<!--ZK_HEADER-->`); 3 ship inline header markup. The placeholder must never leak unsubstituted on any of the 9. |
| HD-02 | P1 | **REFRAMED (Phase 0)** — `header.html` has NO Jinja/f-string/JS-template placeholders today, so there is no live taint surface. HD-02 is a regression guard: static-grep that `header.html` contains no `{{...}}` / `${...}` / `{%...%}` tokens. Future dynamic fields must escape server-side AND extend the allow-list with a comment justifying the new field. |
| HD-03 | P1 | Color rule: no purple anywhere (named / `#A78BFA` / `#7C3AED` / HSL hue 250-290) AND no hard-coded amber (`#D4A024`, `amber`) in `header.css` or `header.html` (amber must come from CSS variables so only `/knowledge-graph` resolves it). Static scan via `static_color_scan` fixture; Playwright computed-style scan via `authed_browser`. |
| HD-04 | P2 | Visual regression at 3 widths (mobile=375, tablet=768, desktop=1280) scoped to the `header.zk-header` locator. Snapshots live in `tests/integration/browser/snapshots/header/`. |
| HD-05 | P2 | JS-collision guard: ensure `document.querySelectorAll('#avatar-btn').length === 1` on every shell'd route and the surviving element is owned by the shared header (`[data-zk-header]`). Cross-references D-2 (home avatar-ID namespace rename) and D-4 (aria-expanded toggle). Static counterpart parses rendered HTML; live counterpart drives Chromium and asserts the click → aria-expanded round-trip. |
| HD-06 | P3 | Asset-integrity sweep: `/header/js/header.js`, `/header/css/header.css`, and all 60 avatar SVGs at `/artifacts/avatars/avatar_NN.svg` for `NN ∈ [00, 59]` must respond 200 with `Content-Length > 0`. Parallel via `asyncio.gather`. |

## Execution order
HD-03 → HD-02 → HD-01 → HD-05 → HD-04 → HD-06

## Industry standards (≤5y)
- OWASP XSS Prevention Cheat Sheet
- Playwright Visual Comparisons
- OWASP WSTG Content Security Policy

## MCP / Chrome usage
- `mcp__Claude_in_Chrome` for HD-01 (visit each of 7 mounted pages, screenshot, assert header DOM present)
- `mcp__Claude_Preview` for HD-04 visual regression baselines
- CI grep for HD-03 static color scan (no Chrome needed)

## Live-test policy
Mocked in CI. `--live` staging. Production read-only — visit all 7 pages unauthenticated, assert header asset 200.
