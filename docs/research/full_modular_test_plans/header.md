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
| HD-01 | P1 | Shell-injection smoke across all 7 inner pages mounted in `app.py` |
| HD-02 | P1 | XSS — dynamic fields injected by `_render_with_shell` escaped |
| HD-03 | P1 | Color rule: no amber outside `/knowledge-graph`, no purple anywhere (static + computed-style scan) |
| HD-04 | P2 | Visual regression at 3 widths (mobile / tablet / desktop) |
| HD-05 | P2 | JS-collision test on RAG/Kastens/Zettels (event-listener leaks) |
| HD-06 | P3 | Asset-integrity check post-deploy (`/header/js/header.js` 200) |

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
