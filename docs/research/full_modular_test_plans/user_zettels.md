# Test Plan — `user_zettels`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`user_zettels`.
Module path: `website/features/user_zettels/`.
Risk tier: **High** (user-scoped content + multi-tenant).

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| Stream load via `/api/graph` + zettel APIs | `js/user_zettels.js` |
| List / filter / pagination / search | `js/user_zettels.js` |
| Zettel detail modal | `js/user_zettels.js` |
| Edit / delete mutations | `js/user_zettels.js` |
| Tag chips & inter-zettel links | `js/user_zettels.js` |

## Tasks

| ID | P | Task |
|---|---|---|
| UZ-01 | P1 | BOLA: user A cannot fetch user B's zettel UUIDs by ID enumeration (API1:2023) |
| UZ-02 | P1 | Cross-workspace isolation under shared Kasten membership |
| UZ-03 | P1 | XSS in zettel title/summary render (script-injection fixtures) |
| UZ-04 | P2 | Pagination/filter/search e2e with large fixture (≥500 zettels) |
| UZ-05 | P2 | Optimistic delete → server failure rollback |
| UZ-06 | P2 | Stale-cache after edit (write-then-read consistency) |
| UZ-07 | P3 | axe-core scan + keyboard nav on stream + modal |

## Execution order
UZ-01 → UZ-02 → UZ-03 → UZ-05 → UZ-06 → UZ-04 → UZ-07

## Industry standards (≤5y)
- OWASP API1:2023 BOLA
- OWASP WSTG §4.7 (Injection)
- Playwright Accessibility Testing
- Reuse `tests/integration/v2/conftest.py` `mint_user` + UUID-leak pattern

## Live-test policy
Mocked Supabase in CI. `--live` staging. Production read-only NOT allowed (would require auth tokens — staging-only).
