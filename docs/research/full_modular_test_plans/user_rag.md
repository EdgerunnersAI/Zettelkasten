# Test Plan — `user_rag`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`user_rag`.
Module path: `website/features/user_rag/`.
Risk tier: **High** (SSE + chat UX + cross-Kasten authz).

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| Chat boot + Kasten/sandbox selector | `js/user_rag.js` |
| SSE stream consumption (`stream:true`, heartbeat-timeout retry) | `js/user_rag.js` |
| `503` retryable bounded-queue UX | `js/user_rag.js` (`_sseRetryUsed` path) |
| Citation rendering | `js/user_rag.js` |
| Session restore | `js/user_rag.js` |
| Example-query content | static fixtures |
| Loader animation | `js/loader.js` |

## Tasks

| ID | P | Task |
|---|---|---|
| UR-01 | P1 | SSE happy-path streaming + token render |
| UR-02 | P1 | SSE reconnect with `Last-Event-ID` after network flap + Cloudflare idle close |
| UR-03 | P1 | `503` rerank-queue saturation → retry UX (no infinite spinner) |
| UR-04 | P1 | Citation HTML sanitization (XSS via citation snippet — untrusted model output) |
| UR-05 | P1 | Cross-Kasten BOLA: chat against Kasten user does not own |
| UR-06 | P1 | No infra leak — no model name / scores / query_class in DOM (CLAUDE.md hard rule) |
| UR-07 | P2 | Multi-tab concurrent chat (no session collision) |
| UR-08 | P2 | Mobile viewport + virtual keyboard handling |
| UR-09 | P2 | aria-live for streamed tokens; axe-core scan |
| UR-10 | P3 | Cross-browser SSE compatibility (Chrome / Safari / Firefox) |

## Execution order
UR-06 → UR-04 → UR-05 → UR-01 → UR-02 → UR-03 → UR-07 → UR-09 → UR-08 → UR-10

## Industry standards (≤5y)
- MDN SSE + Last-Event-ID
- Speakeasy SSE in OpenAPI Best Practices
- Playwright Accessibility Testing — aria-live patterns
- OWASP API1:2023 BOLA
- OWASP XSS Prevention Cheat Sheet

## Live-test policy
Mocked RAG runtime in CI. `--live` staging. Production read-only NOT allowed (chat creation = mutation).
