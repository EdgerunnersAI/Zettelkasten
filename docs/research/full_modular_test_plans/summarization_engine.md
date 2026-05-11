# Test Plan — `summarization_engine`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`summarization_engine`.
Module path: `website/features/summarization_engine/`.
Risk tier: **Critical**.

## Minor modules / sub-flows

| Sub-module | Files |
|---|---|
| API surface | `api/routes.py`, `api/models.py` |
| Core orchestration | `core/router.py`, `core/gemini_client.py`, `core/client_factory.py`, `core/model_factory.py`, `core/cache.py`, `core/telemetry.py`, `core/models.py` |
| Source ingest adapters | `source_ingest/{arxiv,github,hackernews,linkedin,newsletter,podcast,reddit,twitter,web,youtube}/`, `base.py`, `utils.py` |
| Source summarizers | `summarization/{arxiv,github,hackernews,linkedin,newsletter,podcast,reddit,twitter,web,youtube,default}/`, `common/` |
| Batch | `batch/{events,input_loader,processor}.py` |
| Writers | `writers/base.py`, `writers/supabase.py` |
| Evaluator | `evaluator/{atomic_facts,auto_eval_harness,consolidated,next_actions,numeric_grounding,ragas_bridge,rubric_loader,manual_review_writer}.py` |
| UI | `ui/{css,js}` dashboard assets |

## Tasks

| ID | P | Task | Rationale |
|---|---|---|---|
| SE-01 | P1 | SSRF/URL-safety fuzz on `core/router.py` + ingest entry (private IPs, IPv6 loopback, `file://`, `gopher://`, DNS rebinding, redirect-to-internal) | API7:2023 SSRF; capture is trust boundary |
| SE-02 | P1 | Per-adapter contract tests w/ recorded fixtures for all 10 sources | Independent 3rd-party schema drift |
| SE-03 | P1 | Empty/thin extraction rejection + `is_raw_fallback=True` path | Documented invariant |
| SE-04 | P1 | Writer ordering: entitlement consumed only after persisted accept (`writers/supabase.py`) | CLAUDE.md pricing authority |
| SE-05 | P1 | Auth + workspace scoping on `/api/v2/summarize` (BOLA/BFLA matrix) | API1/API5:2023 |
| SE-06 | P1 | Reddit OAuth → public JSON degradation correctness | Chunk-density invariant |
| SE-07 | P1 | Router downgrade cascade (flash → flash-lite → raw fallback) e2e | Production invariant |
| SE-08 | P2 | Batch processor partial-failure + idempotency under retries (`batch/processor.py`) | Amplified blast radius |
| SE-09 | P2 | Nitter pool failover (`source_ingest/twitter/nitter_pool.py`) chaos | Fragile mirror network |
| SE-10 | P2 | Evaluator determinism: rubric loader, atomic facts, numeric grounding regression snapshots | Quality drift |
| SE-11 | P2 | Provider outage/quota chaos with alert assertions | Cascading-failure containment |
| SE-12 | P3 | Cache eviction (`core/cache.py`) TTL boundaries | Polish |
| SE-13 | P3 | Newsletter sub-extractors (`cta`, `preheader`, `stance`, `conclusions`) property-based on malformed HTML | Edge cases |

## Execution order
SE-01 → SE-04 → SE-05 → SE-02 → SE-07 → SE-08 → SE-03 → SE-06 → SE-09 → SE-10 → SE-11 → SE-12 → SE-13

## Industry standards (≤5y)
- OWASP API Security Top 10 (2023) — API1/API5/API7
- MITRE CWE Top 25 (2024) — CWE-918 SSRF
- NIST SSDF SP 800-218
- Pact Contract Testing 2024–25
- agent-chaos (2025) for LLM chaos patterns
- Portkey LLM rate-limit (2024)
- AWS Well-Architected Reliability Pillar — idempotency under retries

## Live-test policy
Mocked by default in CI. `--live` against staging only. Production read-only allowed for `GET /api/v2/*` smoke probes.
