# Test Plan — `user_kastens`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`user_kastens`.
Module path: `website/features/user_kastens/`.
Risk tier: **High** (share/member = direct data-leak surface).

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| Sandbox list / CRUD via `/api/rag/sandboxes*` | `js/user_kastens.js` |
| Share / member add/remove | `js/user_kastens.js` |
| Bulk tag/source-filter add (v2 compat path) | `js/user_kastens.js` |
| Ownership badge / read-only states | `js/user_kastens.js` |

## Tasks

| ID | P | Task |
|---|---|---|
| UK-01 | P1 | BOLA matrix: owner vs member vs non-member CRUD + share permissions |
| UK-02 | P1 | Concurrent add/remove member idempotency (double-click, double-submit) |
| UK-03 | P1 | Cross-tenant denial with UUID-leak assertion (reuse v2 integration pattern) |
| UK-04 | P2 | Bulk-add tag/source filter compatibility regression (v2 bulk-add RPC accepts explicit workspace zettel IDs) |
| UK-05 | P2 | Share-link revoke takes effect immediately (no client cache race) |
| UK-06 | P3 | Visual regression of Kasten cards |

## Execution order
UK-01 → UK-03 → UK-02 → UK-04 → UK-05 → UK-06

## Industry standards (≤5y)
- OWASP API1:2023 BOLA
- OWASP API5:2023 BFLA
- OWASP Multi-Tenant Security Cheat Sheet

## Live-test policy
Mocked Supabase in CI. `--live` staging. Production read-only: NOT allowed for mutations; `GET /api/rag/sandboxes` unauth → assert 401.
