# Test Plan — `knowledge_graph`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`knowledge_graph`.
Module path: `website/features/knowledge_graph/` + `website/api/routes.py` (`/api/graph`).
Risk tier: **High**.

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| UI shell | `index.html`, `js/app.js`, `js/kasten_modal.js`, `css/style.css` |
| File-backed asset | `content/graph.json` |
| `/api/graph` read path | `website/api/routes.py:graph_data` |
| TTL cache | `_graph_cache` per-user, `_graph_cache_global` anonymous, 30s |
| Cache invalidation | `/api/zettels/add` success + nuke routes reset caches |

## Tasks

| ID | P | Task | Rationale |
|---|---|---|---|
| KG-01 | P1 | UI smoke: page boot loads HTML+JS+CSS, fetches `/api/graph`, no console errors | First-line defense |
| KG-02 | P1 | Authz/fallback correctness: anonymous must NOT see another user's nodes; file fallback never leaks v2 content | API1:2023 |
| KG-03 | P1 | `graph.json` integrity CI gate — schema validate committed asset; block dirty runtime captures | Asset hygiene |
| KG-04 | P1 | `/api/graph` v2→file fallback: `KGRepository` raises → file-store served; assert log line; no 500 | Documented runtime invariant |
| KG-05 | P1 | Cache-key tenant isolation: User A's response not served from User B's cache slot | Multi-tenant cheat sheet |
| KG-06 | P1 | Cache invalidation race: summarize / nuke clears both `_graph_cache*` globals under concurrent writes | Race condition |
| KG-07 | P2 | E2E (Playwright): `/knowledge-graph` renders ≥N nodes, kasten_modal opens, link traversal works | UX correctness |
| KG-08 | P2 | Static asset integrity (sha256, no broken refs, no purple in CSS) | Color rule + deploy hygiene |
| KG-09 | P2 | Large-graph render baseline (1k/5k/10k nodes) — FPS + memory budget | Scalability |
| KG-10 | P2 | 30s TTL boundary: t=29s cached, t=31s recompute (time-mocked) | TTL correctness |
| KG-11 | P2 | Concurrency: 50 parallel `/api/graph` calls during cache miss → single recompute or bounded | Thundering-herd |
| KG-12 | P2 | Retired endpoints (`/api/graph/query`, `/search`, `/rebuild-links`) return deprecation code w/ no v1 leak | Migration hygiene |
| KG-13 | P3 | Visual regression snapshots for graph page + modal | Polish |
| KG-14 | P3 | Mobile-redirect interception verified (UA regex routes mobiles away from `/knowledge-graph`) | Edge |
| KG-15 | P3 | Response-size budget on 10k-node graph (latency p95, payload bytes) | Polish |

## Execution order
KG-03 → KG-04 → KG-05 → KG-06 → KG-02 → KG-01 → KG-10 → KG-11 → KG-12 → KG-08 → KG-07 → KG-09 → KG-13 → KG-14 → KG-15

## Industry standards (≤5y)
- OWASP API1:2023 BOLA
- OWASP Multi-Tenant Security Cheat Sheet (2024)
- Visual Computing for Industry — Graph viz benchmarks (2025)
- Neo4j WebGL viz blog
- Playwright Visual Comparisons
- AWS Well-Architected REL11 — fallback patterns
- NIST SP 800-204C — microservice resilience

## Live-test policy
Mocked Supabase in CI. `--live` staging. Production read-only `GET /api/graph` allowed unauthenticated.
