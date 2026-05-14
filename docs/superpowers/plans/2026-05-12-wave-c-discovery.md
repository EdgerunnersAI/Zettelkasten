# WAVE-C Phase 0 — Discovery Findings

Scope: 4 modules (`summarization_engine`, `api_key_switching`, `knowledge_graph`, `kg_features`).
Inspector: Phase-0 discovery subagent. Code only. No edits.

---

## Module: summarization_engine

### Confirmed APIs
| Symbol | Spec ref | Actual file:line | OK? |
|---|---|---|---|
| `core/router.py::detect_source_type` | core/router | `core/router.py:72-100` | OK |
| `core/router.py::classify_youtube_format` | core/router | `core/router.py:110-130` | OK |
| `core/router.py::classify_github_archetype` | core/router | `core/router.py:133-159` | OK |
| `core/orchestrator.py::summarize_url_bundle` | (impl) | `core/orchestrator.py:58-147` | OK (spec mentions `gemini_client`/`client_factory`/`model_factory`/`cache`/`telemetry`/`models`; orchestrator.py exists in addition — see Delta) |
| `core/cache.py` | core/cache | present | OK |
| `core/gemini_client.py` (`TieredGeminiClient`) | core/gemini_client | present | OK |
| `core/client_factory.py`, `core/model_factory.py`, `core/telemetry.py`, `core/models.py` | core | all present | OK |
| `api/routes.py::summarize_v2`, `batch_v2`, `batch_upload_v2`, `batch_stream_v2` | api/routes | `api/routes.py:29,43,55,65` | OK |
| `api/models.py` | api/models | present | OK |
| `writers/base.py`, `writers/supabase.py::SupabaseWriter` | writers | `writers/supabase.py:79-157` | OK; `markdown.py` also exists (not in spec) |
| `evaluator/{atomic_facts,auto_eval_harness,consolidated,next_actions,numeric_grounding,ragas_bridge,rubric_loader,manual_review_writer}.py` | evaluator | all present | OK; **also `evaluator/models.py` and `evaluator/prompts.py`** (not in spec) |
| `batch/{events,input_loader,processor}.py` | batch | all present | OK |
| `ui/{css,js}` | ui | present + `index.html` | OK |
| Source-ingest registry — `register_ingestor`, `get_ingestor`, `list_ingestors`, `_auto_discover` | (impl) | `source_ingest/__init__.py:14-63` | OK |
| Summarizer registry — `register_summarizer`, `get_summarizer`, `list_summarizers`, `_auto_discover` | (impl) | `summarization/__init__.py:14-63` | OK |

### Source ingest registry (10 sources expected) — ALL PRESENT
- arxiv: `ingest.py` ✓
- github: `ingest.py` + `api_client.py` + `architecture.py` ✓
- hackernews: `ingest.py` ✓
- linkedin: `ingest.py` ✓
- newsletter: `ingest.py` + `cta.py` + `preheader.py` + `stance.py` + `conclusions.py` + `site_extractors.py` ✓
- podcast: `ingest.py` ✓
- reddit: `ingest.py` + `pullpush.py` ✓
- twitter: `ingest.py` + `nitter_pool.py` ✓
- web: `ingest.py` ✓
- youtube: `ingest.py` + `tiers.py` ✓
- `base.py` (`BaseIngestor` ABC) + `utils.py` ✓

### Source summarizer registry — **10 sources + `default` + `common` (not 7)**
- All 10 mirrored: arxiv, github, hackernews, linkedin, newsletter, podcast, reddit, twitter, web, youtube ✓
- Plus `default/summarizer.py` ✓
- Plus `common/` (brief_repair, calibration, dense_cache, dense_verify, dense_verify_runner, json_utils, model_trace, prompts, speaker_detector, structured, text_guards) — non-source helpers ✓
- Also `_wrappers.py` at the package root.

### Minor modules — confirmed
All 8 minor groups in spec match reality, with the additions noted above (markdown writer, evaluator models/prompts, summarization `_wrappers.py`, `core/orchestrator.py`, `core/config.py`, `core/errors.py`).

### External deps surfaced
- Gemini (via `TieredGeminiClient` and `GeminiKeyPool`)
- Supabase (writer + DB v2 ContentRepository, billing.pricing_consume_entitlement)
- Reddit (OAuth + pullpush + JSON fallback)
- YouTube (yt-dlp + transcript)
- GitHub (api_client)
- Nitter mirrors (twitter)

### Delta vs spec
1. Spec lists 7 source summarizers; reality is 10 + `default` + `common`. Plan must update test target list.
2. Spec did not call out `core/orchestrator.py`, `core/config.py`, `core/errors.py`, `summarization/_wrappers.py`, `evaluator/models.py`, `evaluator/prompts.py`, `writers/markdown.py`. None contradict the spec — they are the actual entry points the tests need.
3. `_INGEST_CACHE` in `orchestrator.py:31` is a filesystem-backed cache (`FsContentCache(root=_CACHE_ROOT, namespace="ingests")`), not the per-source `core/cache.py`. SE-12 (TTL) will need to test BOTH caches.
4. Spec SE-04 says “entitlement consumed only after persisted accept” — `SupabaseWriter.write` (`writers/supabase.py:104-157`) is where this lives; needs explicit confirmation that the consume_entitlement call sits after the v2 insert.
5. `BaseIngestor.ingest(url, *, config: dict)` is stable; `BaseSummarizer.summarize(ingest)` is stable. Tests can pin to these signatures.

### Plan amendments required (summarization_engine)
- SE-02: list 10 source contract pairs (not 7).
- SE-03: cover `ExtractionConfidenceError` path (`orchestrator.py:117-138`) AND newsletter `NewsletterURLUnreachable` (`orchestrator.py:108-115`) — both pre-Gemini failure modes.
- SE-04: anchor on `SupabaseWriter.write` ordering; assert `_resolve_workspace_id` called BEFORE persist, entitlement consumed AFTER successful insert.
- SE-12: cover both caches (`core/cache.py` and the filesystem `_INGEST_CACHE`).
- New (P2): registry-discovery test — assert `list_ingestors()` and `list_summarizers()` each return all 10 SourceType members; guard against pkgutil drift.

---

## Module: api_key_switching

### Confirmed APIs
| Symbol | Spec ref | Actual file:line | OK? |
|---|---|---|---|
| `init_key_pool()` | __init__ exports | `__init__.py:22-53` | OK |
| `get_key_pool()` | __init__ exports | `__init__.py:56-61` | OK |
| `GeminiKeyPool` class | `key_pool.py` | `key_pool.py:294-648` | OK |
| `GeminiKeyPool.next_attempt`, `generate_content`, `generate_structured`, `embed_content`, `embed_content_safe` | `key_pool.py` | `key_pool.py:387, 446, 552, 599, 643` | OK |
| `GeminiKeyPool._build_attempt_chain`, `_build_embedding_chain`, `_ordered_key_indices`, `_mark_cooldown` | (impl) | `key_pool.py:408, 431, 380, 358` | OK |
| `select_starting_model(content_length, source_type)` | `routing.py` | `routing.py:23-41` | OK |
| `parse_api_env_line`, `normalize_api_keys`, `filter_api_keys_by_role`, `candidate_api_env_paths` | (impl) | `key_pool.py:153, 169, 191, 250` | OK |

### Module structure
- `__init__.py` — exports `init_key_pool`, `get_key_pool`, `_API_ENV_PATHS`, `_load_keys_from_file`. ✓
- `key_pool.py` — pool + cooldowns + retry + attempt chain. ✓
- `routing.py` — content-aware routing (single function `select_starting_model`). ✓
- `api_env` — local file (UNTRACKED, should be `.gitignore`d — verify).

### Key-pool API verification
- Pool bootstrap precedence (spec KP-01): `__init__.py:26-43` walks `_API_ENV_PATHS`, then env CSV `GEMINI_API_KEYS`, then `settings.gemini_api_key`. Matches CLAUDE.md.
- `_API_ENV_PATHS` discovery in `candidate_api_env_paths()` (`key_pool.py:250-284`) — must include `/etc/secrets/api_env` per CLAUDE.md.
- Key-first traversal (KP-02 / CLAUDE.md locked decision): `_build_attempt_chain` (`key_pool.py:408-429`) does `[(ki, model) for model in models for ki in key_indices]` → outer loop = model, inner = key index. **THIS IS MODEL-FIRST IN THE LOOP NOTATION** but produces **KEY-FIRST traversal** because for each model, all keys are exhausted before downgrading. Verify by reading: for `models=[flash, lite]` and `keys=[k1,k2]`, output = `[(k1,flash),(k2,flash),(k1,lite),(k2,lite)]` — that is key-first within a model tier, then model downgrade. **MATCHES CLAUDE.md LOCKED DECISION.** Test KP-02 must pin this exact ordering.
- Content-aware thresholds (`routing.py:11-20`): `_BEST_MODEL="gemini-2.5-flash"`, `_LITE_MODEL="gemini-2.5-flash-lite"`, `_COMPLEX_SOURCES=frozenset({"youtube","newsletter","github"})`, `_SHORT_THRESHOLD=2000`. **PROTECTED KNOB** per CLAUDE.md — must be guarded by KP-06 boundary tests.
- `_GENERATIVE_MODEL_CHAIN = ["gemini-2.5-flash","gemini-2.5-flash-lite"]` (`key_pool.py:19-22`) — 2 tiers, not 3+.

### Delta vs spec
1. Spec mentions `routing.py` with multiple helpers; reality is one function (`select_starting_model`). Plan must drop "routing helper signatures" plural and pin the single signature `(content_length: int, source_type: str | None) -> str`.
2. Spec implies `key_pool.py` has cooldown TTL methods; actual surface uses `_cooldown_for_attempt`, `_mark_cooldown`, `_purge_expired`, `_cooldowns` dict keyed by `(key_index, model)`. KP-05 needs to be re-anchored to these.
3. `_API_ENV_PATHS` is computed once at import time (`__init__.py:18`). KP-10 hot-reload would require restart — spec marks P3, plan should mark "out of scope unless explicitly requested".
4. `embed_content` (sync) + `embed_content_safe` (sync, swallows exceptions, returns `None`/raw) — kg_features `generate_embedding` calls these. Add to KP coverage.
5. Pool also has billing/free role partitioning (`_role_for_key`, `key_role_filter`, `_billing_key_index_override`) — NOT in spec. Plan should note this expanded surface but DEFER detailed billing-role tests to Phase 9 pricing enforcement (per CLAUDE.md).

### Plan amendments required (api_key_switching)
- KP-02 anchor test: pin exact `[(k1,flash),(k2,flash),...,(k1,lite),(k2,lite)]` ordering with 2 keys × 2 models golden snapshot.
- KP-06: add explicit unit on threshold boundaries: 1999 chars + non-complex → lite; 2000 chars + non-complex → flash; complex source @ any length → flash.
- KP-09: `--preload` invariant test should assert `GeminiKeyPool.__init__` does NOT mutate global state post-fork (no module-level singleton write outside `init_key_pool`).
- New (P1): role-filter test — `key_role_filter()` env-driven; `KEY_ROLE` env var must be respected, free + billing partitioning must not collapse to one chain.
- Out-of-spec billing surface (`_billing_key_index_override`, etc.) — DEFERRED to Phase 9 per pricing module authority. Operator approval needed if plan expands.

---

## Module: knowledge_graph

### Confirmed APIs
| Symbol | Spec ref | Actual file:line | OK? |
|---|---|---|---|
| `index.html`, `js/app.js`, `js/kasten_modal.js`, `css/style.css` | UI shell | all present | OK |
| `content/graph.json` | file asset | present | OK |
| `/api/graph` handler `graph_data` | `website/api/routes.py:graph_data` | `routes.py:370-416` | OK |
| `_graph_cache`, `_graph_cache_ts`, `_graph_cache_global`, `_graph_cache_global_ts` | TTL slots | `routes.py:42-43, 279-280` | OK |
| `_GRAPH_CACHE_TTL = 30` | TTL value | `routes.py:44` | OK (matches spec 30s) |

### Cache verification
- TTL: 30s (`_GRAPH_CACHE_TTL = 30`, `routes.py:44`). ✓
- **Two cache slots present (`_graph_cache` + `_graph_cache_global`) BUT `graph_data` handler at `routes.py:370-416` only reads/writes `_graph_cache_global`.** The per-user `_graph_cache` slot is referenced as a global in invalidation paths (`delete_zettel`, `routes.py:486-488`; nuke routes `routes.py:558-561, 707`) but never populated by the read path. **THIS IS A SPEC-VS-CODE DELTA.**
- v2 path (`routes.py:394-402`): when `use_supabase_v2() and user is not None`, calls `_v2_assemble_graph(user_sub=..., limit=..., offset=...)` and returns directly — **NO CACHING for the v2 / per-user path.**
- Cache only applies to default-pagination file-store branch (`use_cache = offset == 0 and limit >= 5000`, `routes.py:408`). Smaller pages bypass cache entirely.
- Invalidation: `delete_zettel`, nuke routes, and `summarize_v2` success paths all clear both globals.

### Static assets
- `index.html` references `/kg/css/style.css?v=20260426d` (cache-bust qs). Color is amber `#D4A024` — confirmed (no purple).
- `js/app.js`, `js/kasten_modal.js` present; `app.js` is the 3D viz, `kasten_modal.js` is the kasten detail modal.

### Delta vs spec
1. **Spec assumed `_graph_cache` is per-user populated.** Reality: only `_graph_cache_global` is written by the handler; `_graph_cache` exists only as an invalidation target. Either dead code OR an unfinished per-user cache slot. **THIS IS A P1 SECURITY-RELEVANT FINDING** — KG-05 (cache-key tenant isolation) becomes simpler (no per-user cache to confuse) but the dead globals should be flagged for cleanup.
2. **v2 / per-user responses are uncached.** No thundering-herd protection on `_v2_assemble_graph` for authenticated users — KG-11 (50 parallel under cache miss) needs to test BOTH the cached file-store path and the always-uncached v2 path. The latter has zero protection today.
3. Spec KG-12 mentions `/api/graph/query`, `/search`, `/rebuild-links` retired. Confirmed: `/api/graph/query` (`routes.py:633`), `/api/graph/search` (`routes.py:670`) return deprecation responses; `/api/graph/rebuild-links` is **HARD DELETED** (no handler, FastAPI returns 404).
4. Spec lists "Cache invalidation: Add Zettel success + nuke routes reset caches" — also need to add `delete_zettel` (`routes.py:486-488`) and an apparent purchase-success path at `routes.py:686-708`.

### Plan amendments required (knowledge_graph)
- KG-05: revise — there is no per-user cache slot in the read path, so the test should ASSERT no cross-tenant leakage AND assert `_graph_cache` (per-user global) is never written by `graph_data` (regression guard against accidental per-user caching without keying).
- KG-06: include `delete_zettel` and the purchase webhook path in the invalidation race matrix.
- KG-11: split into two — (a) cached file-store path (50 parallel → 1 recompute), (b) v2 per-user path (50 parallel → currently 50 recomputes; assert + flag as a Phase-1 finding for operator decision on whether to add a single-flight wrapper).
- New (P2): assert `_graph_cache` global is dead code OR is wired into a future per-user keyed cache. Operator decision needed: either delete the unused globals or add per-user keying.
- KG-08: add an explicit `index.html` no-purple grep + amber `#D4A024` presence assertion.

---

## Module: kg_features

### Confirmed APIs
| Symbol | Spec ref | Actual file:line | OK? |
|---|---|---|---|
| `analytics.py::compute_graph_metrics` | analytics | `analytics.py:63-146` | OK |
| `analytics.py::_build_networkx_graph` | analytics | `analytics.py:37-44` | OK |
| `analytics.py::_compute_with_fallback` | analytics | `analytics.py:47-58` | OK |
| `analytics.py::GraphMetrics` (dataclass) | (impl) | `analytics.py:23-32` | OK |
| `embeddings.py::_normalize_embedding` | embeddings | `embeddings.py:24-32` | OK |
| `embeddings.py::generate_embedding` | embeddings | `embeddings.py:37-63` | OK |
| `embeddings.py::generate_embeddings_batch` | embeddings | `embeddings.py:68-96` | OK |
| `embeddings.py::should_create_semantic_link(similarity, threshold=0.75)` | embeddings | `embeddings.py:101-103` | OK |
| `embeddings.py::find_similar_nodes` | embeddings | `embeddings.py:106-137` | OK (RPC `match_kg_nodes`) |

### Module structure
- Only `analytics.py` + `embeddings.py` (post Phase 8.0-H7 cleanup confirmed). ✓ No `retrieval.py`, `nl_query.py`, `entity_extractor.py` — matches spec.

### Purity audit
- `analytics.py`: NO supabase / insert / upsert / repository / writer references found via grep. Pure-compute over `KGGraph`. ✓
- `embeddings.py`: `find_similar_nodes(supabase_client, ...)` calls `supabase_client.rpc("match_kg_nodes", ...)` which is **READ-ONLY** (vector similarity match). No insert/update/delete. `generate_embedding` and `generate_embeddings_batch` use `pool.embed_content_safe` — also read-only against Gemini. **PURITY HOLDS.**

### Delta vs spec
1. Spec `find_similar_nodes` says "RPC authz: scope by `user_id`" — confirm in `embeddings.py:106-137` that `user_id` is passed into the RPC payload. Need to read `find_similar_nodes` body to verify the RPC args. (Body wasn't fully unfolded in this discovery — Phase 1 must verify before writing KF-03.)
2. Threshold default `0.75` confirmed at `embeddings.py:101`.
3. `_normalize_embedding(raw)` returns `[]` on zero norm (per `embeddings.py:24-32`) — needed for KF-04 zero-vector safety.

### Plan amendments required (kg_features)
- KF-03: Phase 1 must first read `find_similar_nodes` body to confirm `user_id` arg name and RPC payload key, then write the cross-tenant denial test against the actual RPC contract.
- KF-08: regression-gated perf budget — operator decision needed on the budget number (spec hints "<2s @ 5k") given the production droplet is 2 GB / 1 vCPU.
- New (P2): purity-guard CI test — grep `kg_features/*.py` for `insert(`, `upsert(`, `update(`, `delete(`, `\.from_\(.*\)\.insert` etc., fail CI if any DB-write surface re-appears (Phase 8.0-H7 ratchet).

---

## Cross-cutting findings

### Key pool — actual exports + traversal-order verification
- `init_key_pool`, `get_key_pool` (only public exports). `GeminiKeyPool` is the underlying class (consumed via `get_key_pool()`, not exported by name).
- Traversal order CONFIRMED key-first within model tier, then model downgrade — matches CLAUDE.md locked decision.
- 2-model chain (`flash` → `flash-lite`); no third tier.

### Graph 30s cache — location + per-user vs anon split
- Single TTL constant at `website/api/routes.py:44` (`_GRAPH_CACHE_TTL = 30`).
- Two caches DECLARED (`_graph_cache`, `_graph_cache_global`) but only `_graph_cache_global` POPULATED by the read handler.
- v2 / per-user read path has NO caching (no thundering-herd guard).

### kg_features purity
- HOLDS post-Phase 8.0-H7. Only `find_similar_nodes` touches Supabase, via a read-only RPC. No writes anywhere.

### Protected knobs audit (this cluster)
| Knob | Status | Evidence |
|---|---|---|
| `GUNICORN_WORKERS=2` | Not in this cluster (deployment env / `ops/`); not touched by these 4 modules. | n/a |
| `--preload` | Not in this cluster (deployment); pool import-time work runs once pre-fork. | `__init__.py:18-19` |
| BGE int8 quantization | Not in this cluster (RAG reranker, not summarization). | n/a |
| Content-aware routing thresholds (`_SHORT_THRESHOLD=2000`, `_COMPLEX_SOURCES`, `_BEST_MODEL`, `_LITE_MODEL`) | INTACT | `routing.py:11-20` |
| Key-first traversal | INTACT | `key_pool.py:380-385, 408-429` |
| `_GENERATIVE_MODEL_CHAIN = [flash, flash-lite]` | INTACT | `key_pool.py:19-22` |
| Graph cache TTL = 30s | INTACT | `routes.py:44` |
| `kg_features` purity (analytics + embeddings only) | INTACT | dir listing + grep |

---

## Recommended plan amendments (P0)

1. **Summarizer count: 10 + default + common, not 7.** Update spec table and SE-02 checklist to enumerate all 10 contract pairs.
2. **Per-user graph cache is dead code.** `_graph_cache` slot is invalidated everywhere but never written. KG-05 simplifies; add P2 cleanup decision (operator: delete vs implement per-user keying).
3. **v2 / authenticated `/api/graph` path has zero caching or single-flight protection.** Split KG-11 into cached + uncached paths; surface as operator decision for thundering-herd hardening.
4. **`routing.py` has ONE function (`select_starting_model`), not "helpers".** KP-06 must pin signature `(content_length: int, source_type: str | None) -> str` and the 4 threshold/source constants as snapshot fixtures.
5. **Key-first traversal proof.** KP-02 needs a golden 2×2 ordering snapshot — anchor against `_build_attempt_chain` output, not against `next_attempt` alone.
6. **Add registry-completeness gate (P1).** Test `list_ingestors()` and `list_summarizers()` return all 10 SourceType members; pkgutil drift would silently break a source.
7. **Add `kg_features` purity ratchet (P2).** Grep CI fails on any DB-write call surface in `analytics.py`/`embeddings.py` (Phase 8.0-H7 invariant).
8. **Verify `find_similar_nodes` RPC payload before writing KF-03.** Phase 1 must unfold the body and pin the actual `user_id` arg name.
9. **Writer ordering (SE-04).** Pin "consume_entitlement AFTER successful insert" against `SupabaseWriter.write` (`writers/supabase.py:104-157`); add UUID-leak assertion on the cross-tenant denial path.
10. **Pre-Gemini failure paths (SE-03).** Both `ExtractionConfidenceError` and `NewsletterURLUnreachable` raise BEFORE the LLM call — test both.

---

## Operator decisions needed (P0)

- **DECISION 1 (P1 security-relevant):** `_graph_cache` per-user global is unused. Delete the dead globals OR implement per-user keyed caching? (Affects KG-05/KG-06 test scope.)
- **DECISION 2 (scalability):** v2 `/api/graph` per-user path has no thundering-herd protection. Add single-flight wrapper now (Phase 1) or defer to future hardening? (Affects KG-11.)
- **DECISION 3 (perf budget):** KF-08 needs a concrete budget number for `compute_graph_metrics` at 1k/5k/10k nodes on the 2 GB / 1 vCPU droplet. What number?
- **DECISION 4 (billing role coverage):** key_pool has `_billing_key_index_override`, `key_role_filter`, free/billing partitioning. Test in WAVE-C (expands scope) OR defer to Phase 9 pricing enforcement? CLAUDE.md pricing authority says do NOT modify entitlement logic — read-only role tests should be safe.
- **DECISION 5 (registry completeness):** Add the auto-discovery gate (recommended #6) to WAVE-C now, or treat as separate hygiene?

## Phase 1 readiness

**CAN dispatch immediately** for:
- summarization_engine SE-01 (SSRF), SE-02 (per-adapter contracts, with corrected count of 10), SE-03 (extraction failure paths), SE-12 (cache TTL — both caches).
- api_key_switching KP-01 (bootstrap), KP-02 (key-first proof), KP-04 (429 cascade), KP-06 (threshold boundaries), KP-08 (secret hygiene).
- knowledge_graph KG-01 (UI smoke), KG-03 (graph.json schema), KG-08 (no purple, sha256), KG-10 (TTL boundary), KG-12 (retired endpoints).
- kg_features KF-01 (Louvain seed=42), KF-02 (failure-mode contract), KF-04 (L2 normalization), KF-05 (boundary cases), KF-10 (threshold strictness).

**NEEDS DECISION GATE before dispatch:**
- KG-05 / KG-06 / KG-11 — pending DECISION 1 + 2 (per-user cache + thundering-herd).
- KF-03 — pending Phase 1 unfold of `find_similar_nodes` body.
- KF-08 — pending DECISION 3 (perf budget).
- SE-04 — pending verification of consume_entitlement ordering inside `SupabaseWriter.write` body (need full read).
- KP additions for billing roles — pending DECISION 4.

## New fixture requirements

- `mock_gemini_pool` — fakes `GeminiKeyPool.generate_content` / `generate_structured` / `embed_content_safe` with deterministic chain replay; supports forced 429 + cooldown injection per (key_index, model).
- `mock_supabase_kg_v2` — already exists in `tests/integration/v2/conftest.py` per CLAUDE.md; reuse for `/api/graph` v2 path tests. Confirm.
- `recorded_source_fixtures` — per-source HTML/JSON cassette set for the 10 ingestors (SE-02). VCR.py or hand-rolled fixtures dir.
- `nx_graph_factory` — deterministic NetworkX graph builder for KF-01/05/06 (seed=42).
- `graph_json_loader` — schema-validated loader for `content/graph.json` (KG-03).
- `frozen_clock` — for KG-10 TTL boundary and KP-05 cooldown-clock-skew tests (already present in some test suites; verify reuse).
- `mint_test_user_with_workspaces` — already in `tests/v2/fixtures/users.py`; reuse for SE-05 BOLA matrix and KF-03 cross-tenant denial.
