# Test Plan — `browser_cache`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`browser_cache`.
Module path: `website/features/browser_cache/`.
Risk tier: **Moderate** (non-authoritative; misuse = soft auth bugs).

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| `STATE_KEY` localStorage (TTL 180d, 256B cap) | `js/cache.js` |
| `RETURN_KEY` sessionStorage (TTL 15m, 96B cap) | `js/cache.js` |
| `isPath()` guard (rejects `//`, requires `/`, ≤128 chars) | `js/cache.js` |
| `normalizeState` corruption recovery | `js/cache.js` |
| Public API: `markLoggedIn` / `markLoggedOut` / `consumeReturnPath` | `js/cache.js` |

## Tasks

| ID | P | Task |
|---|---|---|
| BC-01 | P1 | Secret-leak invariant — no JWT / refresh / email / UUID ever in storage values |
| BC-02 | P1 | `setReturnPath` rejects `//evil`, `http://...`, `javascript:`, `\\evil`, `/` w/ embedded `\n` |
| BC-03 | P1 | Return-path round-trip: set → login → consume → cleared |
| BC-04 | P2 | TTL expiry: state expires at 180d, return at 15m (time-mocked) |
| BC-05 | P2 | Malformed JSON / quota-exceeded recovery (private mode, Safari ITP) |
| BC-06 | P2 | Byte-cap enforcement (`MAX_STATE_BYTES`, `MAX_RETURN_BYTES`) — DoS guard |
| BC-07 | P2 | Cross-browser matrix (Safari ITP 7-day cap, Firefox strict mode) |

## Execution order
BC-02 → BC-01 → BC-03 → BC-06 → BC-04 → BC-05 → BC-07

## Industry standards (≤5y)
- OWASP WSTG §4.11.12 Testing Browser Storage
- OWASP HTML5 Security Cheat Sheet — Web Storage
- OWASP Session Management Cheat Sheet

## MCP / Chrome usage
- `mcp__Claude_in_Chrome` for BC-03 (live round-trip via auth callback)
- `mcp__Claude_in_Chrome` for BC-07 (Safari + Firefox emulation if supported)
- Jest/Vitest jsdom for BC-01/02/04/06 (no browser needed)

## Live-test policy
jsdom-mocked in CI. `--live` staging only.
