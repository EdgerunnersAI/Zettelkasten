# Test Plan — `rag_pipeline`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`rag_pipeline`.
Module path: `website/features/rag_pipeline/`.
Risk tier: **Critical**.

## Minor modules / sub-flows

| Sub-module | Files |
|---|---|
| Orchestration | `orchestrator.py`, `service.py`, `types.py`, `errors.py` |
| Query | `query/{router,rewriter,transformer,vague_expander,metadata,blocklist}.py` |
| Ingest | `ingest/{chunker,embedder,upsert,content_selection,hook,metadata_enricher,entity_canonicalizer}.py` |
| Retrieval | `retrieval/{hybrid,planner,graph_score,entity_anchor,anchor_seed,kasten_freq,chunk_share,candidate_model,cache,_async_helpers}.py` |
| Rerank | `rerank/{cascade,model_manager,degradation_log}.py` |
| Context | `context/{assembler,distiller}.py` |
| Generation | `generation/{llm_router,gemini_backend,claude_backend,sanitize,prompts,_routing}.py` |
| Critic | `critic/answer_critic.py` |
| Memory | `memory/{session_store,sandbox_store}.py` |
| Scoring | `scoring/{runtime,registry_adapter,registry_init}.py` |
| Backends | `backends/websearch.py` |
| Adapters | `adapters/{pool_factory,gemini_chonkie}.py` |
| Observability | `observability/{metrics,tracer,kasten_stats,event_loop_monitor,anchor_seed_bandit}.py` |
| Evaluation | `evaluation/{ragas_runner,deepeval_runner,composite,eval_runner,component_scorers,synthesis_score,ablation,gold_loader,kg_snapshot,kg_recommender,stress_fixtures,types,_schemas}.py` |

## Tasks

| ID | P | Task | Rationale |
|---|---|---|---|
| RP-01 | P1 | UUID-scoped authz matrix on `orchestrator.process()` + `memory/session_store.py` (BOLA cross-tenant w/ UUID-leak assertions) | API1:2023 |
| RP-02 | P1 | Rerank bounded-queue saturation → retryable 503 backpressure (`rerank/cascade.py`, `rerank/model_manager.py`) | Protected knob Phase 1B.2 |
| RP-03 | P1 | Kasten store membership enforcement (`memory/sandbox_store.py`) | Share/member leakage |
| RP-04 | P1 | Prompt injection / sanitization (`generation/sanitize.py`) payload boundary fuzz | LLM trust boundary |
| RP-05 | P1 | SSE streaming with heartbeat, Last-Event-ID reconnect, Cloudflare idle handling | Phase 1B.4 invariant |
| RP-06 | P1 | Citation integrity: every claim ↔ chunk_id round-trip (`critic/answer_critic.py`, `context/assembler.py`) | Quality + trust |
| RP-07 | P2 | Hybrid retrieval concurrency under load (`retrieval/hybrid.py`, `_async_helpers.py`) | Performance |
| RP-08 | P2 | LLM router cascade chaos (`generation/llm_router.py`) — Gemini outage → Claude fallback | Resilience |
| RP-09 | P2 | Post-answer write idempotency under retries (`memory/session_store.py`) | Idempotency |
| RP-10 | P2 | Query router classification regression (`query/router.py`) + blocklist enforcement | Quality |
| RP-11 | P2 | Embedder + chunker boundaries on oversized / empty / non-UTF8 | Robustness |
| RP-12 | P2 | Event-loop monitor never blocks user response (`observability/event_loop_monitor.py`) | Observability hygiene |
| RP-13 | P2 | Scoring registry adapter contract (mandatory Python adapter per locked decision) | DB refactor locked-decision |
| RP-14 | P3 | Evaluation runners (ragas/deepeval) determinism harness | Eval polish |
| RP-15 | P3 | Anchor-seed bandit observability sanity | Polish |

## Execution order
RP-01 → RP-02 → RP-03 → RP-05 → RP-04 → RP-06 → RP-08 → RP-09 → RP-13 → RP-07 → RP-10 → RP-11 → RP-12 → RP-14 → RP-15

## Industry standards (≤5y)
- OWASP API1/API5 (2023)
- MDN SSE + Last-Event-ID
- FastAPI SSE (2025)
- LoadForge SSE load testing (2024)
- agent-chaos LLM chaos (2025)
- AWS Reliability Pillar (REL11)
- Azure Transient Fault Handling
- SRE Book — Cascading Failures (foundational)

## Live-test policy
Mocked Gemini/Claude in CI. `--live` staging only. Production read-only `GET /api/rag/sessions/*` allowed; no chat creation.
