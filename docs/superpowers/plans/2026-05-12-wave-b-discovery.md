# WAVE-B Phase 0 — Discovery Findings

**Scope:** rag_pipeline + user_kastens + user_zettels + user_rag
**Date:** 2026-05-12
**Status:** Verify-only. No code touched, no tests written, no SQL/infra knobs modified.

---

## Module: rag_pipeline

Module path: `website/features/rag_pipeline/`

### Confirmed APIs

| Symbol | Spec ref | Actual file:line | OK? |
|---|---|---|---|
| `RAGRuntime` (dataclass) | service.py | `service.py:54-59` | YES |
| `_build_runtime(user_sub)` | service.py | `service.py:63-114` (lru_cache 16) | YES |
| `get_rag_runtime(user_sub)` | service.py | `service.py:117-118` | YES |
| `load_example_queries()` | service.py | `service.py:122-131` (lru_cache 1) | YES |
| `RAGOrchestrator` | orchestrator.py | `orchestrator.py:496-1321` | YES |
| `RAGOrchestrator.answer` | orchestrator.py | `orchestrator.py:527-545` (`@observe`) | YES |
| `RAGOrchestrator.answer_stream` | orchestrator.py | `orchestrator.py:548-598` (`@observe`) | YES |
| `RAGOrchestrator._prepare_query` | orchestrator.py | `orchestrator.py:601-717` | YES |
| `RAGOrchestrator._retrieve_context` | orchestrator.py | `orchestrator.py:769-888` | YES |
| `RAGOrchestrator._generate_once` | orchestrator.py | `orchestrator.py:891-917` | YES |
| `RAGOrchestrator._generate_streaming` | orchestrator.py | `orchestrator.py:920-952` | YES |
| `RAGOrchestrator._finalize_answer` | orchestrator.py | `orchestrator.py:955-1214` | YES |
| `CascadeReranker.rerank` | rerank/cascade.py | `rerank/cascade.py:437-505` | YES |
| `ModelManager` | rerank/model_manager.py | `rerank/model_manager.py:21-94` | YES |
| `ChatSessionStore` | memory/session_store.py | `memory/session_store.py:37-189` | YES |
| `SandboxStore` | memory/sandbox_store.py | `memory/sandbox_store.py:31-169` | YES |
| `sanitize_answer` | generation/sanitize.py | `generation/sanitize.py:44-69` | YES |
| `strip_invalid_citations` | generation/sanitize.py | `generation/sanitize.py:72-103` | YES |
| `AnswerCritic.verify` | critic/answer_critic.py | `critic/answer_critic.py:36-68` | YES |
| `RegistryAdapter` | scoring/registry_adapter.py | `scoring/registry_adapter.py:23-104` | YES |

### Minor modules (confirmed against filesystem)

All spec-listed sub-files exist EXCEPT one delta:

| Sub-module | Spec | Actual | Delta |
|---|---|---|---|
| Query | `{router,rewriter,transformer,vague_expander,metadata,blocklist}` | matches | none |
| Ingest | `{chunker,embedder,upsert,content_selection,hook,metadata_enricher,entity_canonicalizer}` | matches | none |
| Retrieval | `{hybrid,planner,graph_score,entity_anchor,anchor_seed,kasten_freq,chunk_share,candidate_model,cache,_async_helpers}` | matches | none |
| Rerank | `{cascade,model_manager,degradation_log}` | matches | none |
| Context | `{assembler,distiller}` | matches | none |
| Generation | `{llm_router,gemini_backend,claude_backend,sanitize,prompts,_routing}` | matches | none |
| Critic | `answer_critic` | matches | none |
| Memory | `{session_store,sandbox_store}` | matches | none |
| Scoring | `{runtime,registry_adapter,registry_init}` | matches | none |
| Backends | `websearch` | matches | none |
| Adapters | `{pool_factory,gemini_chonkie}` | matches | none |
| Observability | `{metrics,tracer,kasten_stats,event_loop_monitor,anchor_seed_bandit}` | matches | none |
| Evaluation | `{ragas_runner,deepeval_runner,composite,eval_runner,component_scorers,synthesis_score,ablation,gold_loader,kg_snapshot,kg_recommender,stress_fixtures,types,_schemas}` | matches | none |

### Delta vs spec

1. **RP-01 spec says `orchestrator.process()`.** Actual public entry points are `RAGOrchestrator.answer(...)` and `RAGOrchestrator.answer_stream(...)`. No method named `process` exists in the orchestrator. The chat surface (`api/chat_routes.py:78`) calls `get_rag_runtime(user["sub"]).orchestrator.answer/answer_stream`, not `process()`.
2. **RP-13 spec assumes a Python registry adapter.** Confirmed real at `scoring/registry_adapter.py` (`RegistryAdapter` class with `start/stop/get_weight/get_params/refresh/_poll_loop/_listen_loop`). Locked decision honored.

### Plan amendments required

- **RP-01:** Target `orchestrator.answer` + `orchestrator.answer_stream` (not `process`). Add UUID-leak assertions per existing pattern from `tests/integration/v2/test_cross_tenant_denial.py`.
- **RP-02:** Bounded-queue tests should target `website/api/_concurrency.py:acquire_rerank_slot` (lines 91-112) — NOT `rerank/cascade.py` directly. The semaphore lives at the HTTP layer, not in the cascade module. `RAG_RERANK_CONCURRENCY` env default = 2.

---

## Module: user_kastens

Module path: `website/features/user_kastens/`

### Confirmed structure

- `index.html`, `css/`, `js/user_kastens.js` (442 lines) — single JS file as spec stated.
- Backend surface: `website/api/sandbox_routes.py` exposes `/api/rag/sandboxes*` + share/member endpoints.

### Confirmed APIs (backend sandbox surface that user_kastens.js calls)

| Endpoint | sandbox_routes.py line | Auth | OK? |
|---|---|---|---|
| `GET /sandboxes` (list) | 308-326 | `get_current_user` | YES |
| `POST /sandboxes` (create) | 329-407 | `get_current_user` | YES |
| `GET /sandboxes/{id}` | 410-423 | `get_current_user` | YES |
| `PATCH /sandboxes/{id}` | 455-473 | `get_current_user` | YES |
| `DELETE /sandboxes/{id}` | 476-499 | `get_current_user` | YES |
| `POST /sandboxes/{id}/share` | 502-558 | `get_current_user` | YES |
| `GET /sandboxes/{id}/members` | 426-452 | `get_current_user` | YES |
| `POST /sandboxes/{id}/members` (bulk add) | 561-634 | `get_current_user` | YES |
| `DELETE /sandboxes/{id}/members/{node_id}` | 637-647 | `get_current_user` | YES |
| `DELETE /sandboxes/{id}/members` (bulk remove) | 650-679 | `get_current_user` | YES |
| `GET /nodes` (list_user_nodes) | 287-305 | `get_current_user` | YES |

### Authentication boundary (UUID enforcement)

Confirmed: `sandbox_routes.py:50-65 _v2_scope_for(user)` does `UUID(str(value))` validation via `_is_uuid()` at line 40-47, and `Depends(get_current_user)` is on every endpoint. UUID-shaped path parameters (`sandbox_id: UUID`) FastAPI-coerced.

### Delta vs spec

1. Spec assumes `bulk-add v2 compat path` lives "in user_kastens.js". The server-side gate is at `sandbox_routes.py:585` — `wz_ids = [UUID(nid) for nid in body.node_ids]` only accepts UUID-shaped node_ids (workspace_zettel_ids). Tag-mode and source-type-filtered bulk-add go through a different code path. Spec wording is consistent but the test target needs both the JS surface AND `sandbox_routes.py:561-634`.

### Plan amendments required

- **UK-04 (bulk-add compat):** Test target = JS `bulkAdd*` flow + backend `add_members` at `sandbox_routes.py:561-634`. Confirm `tag_mode` validator (lines 178-184) accepts `any|all` only.

---

## Module: user_zettels

Module path: `website/features/user_zettels/`

### Confirmed structure

- `index.html`, `css/`, `js/user_zettels.js` (1957 lines) — single JS file as spec.
- No `__init__.py` (frontend-only module). Backend surface = `website/api/api.py` (zettels routes) + `website/api/graph_routes.py` + KG node routes.

### Delta vs spec

1. Spec table column "File" says everything maps to `js/user_zettels.js`. Verified — all spec sub-modules (stream load, list/filter, modal, mutation, tag chips) are in that single 1957-line file. No structural drift.

### Plan amendments required

- None on structure. UZ-01..03 targeting unchanged. UZ-04 (≥500 zettels fixture) needs a `mint_user(workspace_count=1)` + bulk-insert helper that does NOT yet exist (see Cross-cutting findings).

---

## Module: user_rag

Module path: `website/features/user_rag/`

### Confirmed structure

| Sub-flow | Spec | Actual file:line | OK? |
|---|---|---|---|
| Chat boot + selector | `js/user_rag.js` | `js/user_rag.js` (1272 lines) | YES |
| SSE stream + heartbeat | `js/user_rag.js` | `js/user_rag.js:705-754` (heartbeat consumer, 15s timeout) | YES |
| `503` retry UX `_sseRetryUsed` | `js/user_rag.js` | `js/user_rag.js:537-543` (503 + Retry-After) + 625-675 (`_sseRetryUsed`) | YES |
| Citation rendering | `js/user_rag.js` | `js/user_rag.js` | YES |
| Session restore | `js/user_rag.js` | `js/user_rag.js` | YES |
| Example queries | `chat_routes.py:402-405` (`/example-queries`) | YES |
| Loader animation | `js/loader.js` | `js/loader.js` (126 lines) | YES |

### Confirmed SSE invariants

- Heartbeat producer: `website/api/chat_routes.py:234-270 _heartbeat_wrapper` emits `: heartbeat\n\n` every 10s (Phase 1B.4 invariant).
- Heartbeat consumer: `js/user_rag.js:705-754` — `lastFrameMs` watchdog at 15s; on stale frame fires `heartbeat-timeout` error → cancels reader → one-shot reconnect via `_sseRetryUsed` flag.
- 503 backpressure path: `chat_routes.py:488-498` (sessions stream), `chat_routes.py:537-552` (adhoc stream), `chat_routes.py:195-207` (non-stream). All set `Retry-After: 5`.

### Delta vs spec

1. UR-02 spec mentions `Last-Event-ID` reconnect. **Actual JS does NOT use the standard browser `EventSource` API with `Last-Event-ID` header.** The client uses `fetch()` + manual `getReader()` over the response body and reconnects by POSTing a fresh request on `heartbeat-timeout`. There is no `Last-Event-ID` resumption — reconnects start from token zero. This is a deliberate architectural choice (POST-body chat carries the prompt; standard EventSource is GET-only).

### Plan amendments required

- **UR-02:** Re-scope from "Last-Event-ID reconnect" to "heartbeat-timeout → full re-POST reconnect" matching actual `_sseRetryUsed` flow. Drop the Last-Event-ID assertion. Test target: simulate idle >15s after first token → expect single retry, no infinite spinner.

---

## Cross-cutting findings

### Rerank bounded queue + 503 path (Protected knob — Phase 1B.2)

**Status: IN PLACE, no drift.**

- Location: `website/api/_concurrency.py`
- Public surface: `acquire_rerank_slot()` async-context-manager at line 91-112; `QueueFull` exception at 58-59; `queue_depth()` at 115-116; `reset_for_tests()` at 119-122.
- Env knobs: `RAG_RERANK_CONCURRENCY` (default 2), `RAG_QUEUE_MAX` (per docstring lines 5-11).
- 503 emission: `chat_routes.py:202-207` (sessions non-stream), `488-498` (sessions stream), `537-552` (adhoc), `_memory_guard.py:104-107`. All include `Retry-After: 5`.
- Concurrency state caches env at first read; `_ConcurrencyState.env_changed()` at 73-77 detects rebind under test.

### SSE wrapper + heartbeat (Protected knob — Phase 1B.4)

**Status: IN PLACE, no drift.**

- Producer: `website/api/chat_routes.py:234-270 _heartbeat_wrapper(inner)` — 10s interval, `: heartbeat\n\n` SSE comments injected alongside real frames. Called by `_stream_answer_with_backpressure` (273-298) and adhoc path (550).
- Consumer: `js/user_rag.js:705-754` — 15s `lastFrameMs` watchdog; on stale frame raises `heartbeat-timeout`.
- Cloudflare idle-502 mitigation is the documented rationale (chat_routes.py:235 docstring).

### Fixture surface

**`tests/integration/v2/conftest.py` provides:**

- `asyncpg_pool` (line 34-59) — async iterator yielding asyncpg.Pool.
- `mint_user` (line 112-127) — factory `_mint(workspace_count=1)` returning `MintedUser` (with `email` field per Phase 8.0-TX).
- `pytest_sessionfinish` (line 130-187) — cleanup hook.

**Missing for WAVE-B:**

- No `mint_kasten`/`mint_sandbox` fixture exists. Searched both `tests/integration/v2/` and `tests/` overall. The closest is direct-SQL inserts inside `test_kasten_share_e2e.py` / `test_sandbox_routes_v2.py`.
- No bulk-zettel-insert helper for UZ-04 (≥500 zettels).

### Protected-knob audit

| Knob | Status | Location |
|---|---|---|
| Rerank semaphore (Phase 1B.2) | INTACT | `api/_concurrency.py:91-112` |
| SSE heartbeat wrapper (Phase 1B.4) | INTACT | `api/chat_routes.py:234-270` |
| 503 + Retry-After path | INTACT | `chat_routes.py:202,495,545`; `_memory_guard.py:106` |
| `RAG_RERANK_CONCURRENCY` default = 2 | INTACT | `_concurrency.py:68` |
| `--preload` / 2-worker gunicorn | not in this scope | (deployment, not feature code) |

No drift detected on any protected knob within Wave-B's scope.

---

## Recommended plan amendments (P0)

1. **RP-01 target rewrite.** Spec → `orchestrator.process()`. Actual → no `process()`; entrypoints are `RAGOrchestrator.answer` (orchestrator.py:527) and `RAGOrchestrator.answer_stream` (orchestrator.py:548). Amendment: rewrite RP-01 task to test BOLA matrix against `answer`/`answer_stream` + `ChatSessionStore.{create_session,list_sessions,list_messages,get_session,append_*}` (memory/session_store.py) with UUID-leak assertions.

2. **RP-02 target relocation.** Spec → "rerank/cascade.py, rerank/model_manager.py". Actual → the bounded queue + 503 backpressure lives at the HTTP layer in `website/api/_concurrency.py:acquire_rerank_slot` and is emitted by `chat_routes.py:202/495/545`. The cascade module itself has NO semaphore. Amendment: target `api/_concurrency.py` + `chat_routes.py` 503 surface. Keep cascade module on the test list only for `MemoryPressureError` (cascade.py:62-74) coverage.

3. **UR-02 SSE reconnect re-scope.** Spec → `Last-Event-ID` reconnect. Actual → POST-body fetch + manual reader + `_sseRetryUsed` one-shot reconnect on `heartbeat-timeout` (user_rag.js:625-675, 705-754). No browser EventSource, no Last-Event-ID header. Amendment: re-scope UR-02 to test heartbeat-timeout → re-POST reconnect (one retry only); drop Last-Event-ID assertion.

4. **New fixture work (blocks UK-01, UK-03, UR-05, UZ-01, UZ-02).** No `mint_kasten`/`mint_sandbox` fixture exists. Amendment: Phase 1 must include a new fixture in `tests/integration/v2/conftest.py` (or sibling module) that wraps `_v2_scope_for` flow to create a Kasten and seed members. Without this, BOLA matrix tests must hand-roll SQL inserts.

5. **New bulk-zettel-insert helper (blocks UZ-04).** No helper for ≥500 zettels exists. Amendment: add a `bulk_insert_zettels(workspace_id, count)` helper for pagination-scale tests.

---

## Operator decisions needed (P0)

- **Q1 (RP-01 retarget):** Approve rewriting RP-01 from `process()` to `answer`/`answer_stream` + `ChatSessionStore`?
- **Q2 (RP-02 retarget):** Approve moving RP-02 target from `rerank/cascade.py` to `api/_concurrency.py` + chat-routes 503 surface? (Cascade keeps a small slice for `MemoryPressureError`.)
- **Q3 (UR-02 re-scope):** Approve dropping Last-Event-ID assertion and testing the actual `_sseRetryUsed` one-shot retry path instead?
- **Q4 (new fixtures):** Approve adding `mint_kasten`/`mint_sandbox` + `bulk_insert_zettels` to `tests/integration/v2/conftest.py` as part of Phase 1 setup? (Non-protected, additive — no infra impact.)
- **Q5 (P1 security flags):** None surfaced beyond spec — UUID auth + workspace gating intact across all four modules. No new vulnerabilities discovered during discovery.

No P1 security issues found. No spec assumption requires deeper research before Phase 1 — all four amendments above are mechanical retargets, not design questions.
