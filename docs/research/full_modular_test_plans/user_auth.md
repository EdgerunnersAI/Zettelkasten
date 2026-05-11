# Test Plan — `user_auth`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`user_auth`.
Module path: `website/features/user_auth/`.
Risk tier: **High**.

## Minor modules / sub-flows

| Sub-module | File / surface |
|---|---|
| Callback page render + meta-refresh fallback | `callback.html` |
| Supabase session bootstrap (hash/query fragment) | `js/auth.js` |
| Return-path reconciliation | `browserCache.consumeReturnPath()` |
| Error UX (denied / expired / missing state) | `js/auth.js` error paths |
| Auth-state listener / signed-in transition | `js/auth.js` |

## Tasks

| ID | P | Task |
|---|---|---|
| UA-01 | P1 | E2E Supabase callback happy-path → `/home` |
| UA-02 | P1 | Open-redirect / return-path tamper — `//evil`, `\\evil`, `http://evil`, `javascript:`, embedded `\n` → rejected by `isPath()` |
| UA-03 | P1 | Expired / replayed / malformed token UX (no infinite spinner, clean error) |
| UA-04 | P1 | OAuth `state` CSRF mismatch handling — mismatched/missing state → rejected |
| UA-05 | P1 | Browser-storage secret-leak scan — no JWT / refresh / email in localStorage/sessionStorage |
| UA-06 | P2 | Cross-browser callback parsing (Chrome / Safari / Firefox) — hash-fragment differences |
| UA-07 | P2 | Mobile UA → `/m/` redirect does not break `/auth/callback` |
| UA-08 | P3 | axe-core WCAG 2.2 AA scan of callback page |

## Execution order
UA-04 → UA-02 → UA-05 → UA-03 → UA-01 → UA-06 → UA-07 → UA-08

## Industry standards (≤5y)
- Curity SPA Best Practices (PKCE, state, redirect_uri allowlist)
- OAuth.com Single-Page Apps (state CSRF)
- Okta SPA Auth Tokens
- OWASP API2:2023 Broken Authentication
- OWASP WSTG §4.11.12 Browser Storage
- OWASP Session Management Cheat Sheet
- Playwright Accessibility Testing (axe-core, WCAG 2.2)

## Live-test policy
Mocked Supabase auth in CI. `--live` against staging Supabase. Production read-only — visit `/auth/callback` with no token (negative-path only).
