# Website Feature Layer

`website/features/` owns the product feature packages used by the FastAPI website: feature-specific UI assets, domain services, routers, registries, monitoring helpers, and runtime subsystems. `website/app.py`, `website/api/`, and `website/core/` compose these features into the running app.

This file is the folder-level map for the feature layer. It is intentionally broader than the deeper feature docs such as `summarization_engine/About.md` and `browser_cache/About.md`.

## What This Folder Owns

- Feature-owned browser surfaces mounted by `website/app.py`: knowledge graph, auth callback assets, signed-in home, zettels, kastens, RAG chat, shared header, browser cache helper, pricing launcher assets, and the summarization-engine dashboard.
- Feature-owned Python subsystems for Gemini key pooling, summarization, KG analytics/embeddings, RAG retrieval/chat, pricing/payments/entitlements, and Slack-backed web monitoring.
- Registries and extension points inside the summarization engine: source ingestors, source-specific summarizers, batch processors, writers, evaluator helpers, and `/api/v2` route models.
- RAG runtime components: query routing/rewriting, retrieval, graph scoring, cascade reranking, context assembly, LLM backends, answer criticism, sessions, kastens/sandboxes, chunk ingestion, evaluation, scoring, and observability.
- Feature-local docs and guardrails in existing `CLAUDE.md` and `About.md` files.

## What This Folder Does Not Own

- FastAPI app assembly, global middleware, mobile redirects, static mounts, and shared HTML shell injection. Those live in `website/app.py`.
- Most legacy/public API handlers, including `/api/summarize`, `/api/graph`, zettel mutation, RAG chat routes, sandbox/Kasten APIs, auth dependencies, and Nexus APIs. Those live under `website/api/`, although they call services in this folder.
- Core persistence, URL normalization, settings, graph file-store mutation, Supabase v2 clients/repositories, and DB version gating. Those live under `website/core/`.
- Supabase SQL schema ownership. That lives under `supabase/`.
- Mobile page assets, footer about/pricing pages, experimental Nexus/PageIndex code, deployment scripts, and root project docs.
- Secret storage. Feature code reads configured environment variables, but this folder must not contain real `.env` files or committed credentials.

## Feature Inventory

| Path | Responsibility | Main integration points |
|---|---|---|
| `api_key_switching/` | Shared Gemini key pool, key discovery, cooldowns, retries, embedding calls, and content-aware routing helpers | Used by summarization, KG embeddings, RAG embedding/generation, and eval tooling |
| `browser_cache/` | Non-secret browser-side UX hints and auth return-path storage | Mounted at `/browser-cache/js`; consumed by landing/auth callback flows |
| `header/` | Shared header fragment, CSS, and JS for inner pages | Injected by `website/app.py::_render_with_shell()` and mounted at `/header/*` |
| `knowledge_graph/` | Desktop graph UI assets and file-backed public graph data asset | Served at `/knowledge-graph`, `/kg/*`, and indirectly through `/api/graph` fallback |
| `kg_features/` | Graph analytics and embedding utilities | `/api/graph` calls `compute_graph_metrics()`; persistence can call `generate_embedding()` |
| `rag_pipeline/` | Authenticated RAG runtime over zettels and Kastens | Used by `/api/rag/*` and `/api/rag/sandboxes*` routes |
| `summarization_engine/` | v2 URL ingestion, summarization, batch, writers, evaluator, dashboard, and `/api/v2` routes | Called by `website/core/pipeline.py` and mounted by `website/app.py` |
| `user_auth/` | Supabase auth callback page and auth client assets | Served at `/auth/callback` and mounted under `/auth/*` |
| `user_home/` | Signed-in home page assets | Served at `/home`; uses shared header/footer and API auth state |
| `user_zettels/` | Personal zettel stream UI | Served at `/home/zettels`; talks to graph/zettel APIs |
| `user_kastens/` | Kasten management UI | Served at `/home/kastens`; talks to sandbox/Kasten APIs |
| `user_rag/` | RAG chat UI and example-query content | Served at `/home/rag`; talks to `/api/rag/*` |
| `user_pricing/` | Pricing catalog, entitlement checks, Razorpay client/repository, payment routes, subscription handling, checkout launcher | Router included by `website/app.py`; APIs are under `/api/pricing/*` and `/api/payments/*` |
| `web_monitor/` | Slack notifications and monitoring routers for app errors, user activity, DigitalOcean alerts, pricing visits, and payments | Router included by `website/app.py`; global exception handler calls `notify_app_error()` |

## Entry Points And Public Interfaces

- `website/app.py:create_app()` includes feature routers from `summarization_engine.api`, `user_pricing.routes`, and `web_monitor`.
- `website/app.py:create_app()` mounts feature assets under `/kg/*`, `/auth/*`, `/browser-cache/*`, `/home/*`, `/user-pricing/*`, `/header/*`, `/artifacts`, and `/summarization-engine/*`.
- `website/api/routes.py` exposes `/api/summarize`, `/api/graph`, and zettel mutation routes. It calls `website.core.pipeline`, `website.core.persist`, `kg_features.analytics`, and `user_pricing.entitlements`.
- `website/api/chat_routes.py` exposes `/api/rag/*` chat/session endpoints and streams answers through `rag_pipeline.service.get_rag_runtime()`.
- `website/api/sandbox_routes.py` exposes `/api/rag/nodes` and `/api/rag/sandboxes*` Kasten/sandbox endpoints backed by RAG memory and Supabase v2 repositories.
- `summarization_engine.api.routes` exposes `/api/v2/summarize`, `/api/v2/batch`, `/api/v2/batch/upload`, and `/api/v2/batch/stream`.
- `user_pricing.routes` exposes pricing catalog, billing profile, payment order/subscription, subscription change/cancel/status, payment verification, webhook, and payment-status endpoints.
- `web_monitor.__init__` exports an aggregate router plus `notify_app_error`, `notify_new_signup`, `notify_pricing_visit`, and `notify_payment`.
- `api_key_switching.__init__` exports `init_key_pool()` and `get_key_pool()` as shared key-pool singleton accessors.
- `summarization_engine.source_ingest` and `summarization_engine.summarization` expose `register_*`, `get_*`, and `list_*` registry functions for ingestors and summarizers.

## Representative Runtime Flows

### Public Or Signed-In URL Capture

1. The browser posts a URL to `/api/summarize`.
2. `website/api/routes.py` validates the request, rate-limits by IP, and checks zettel entitlement through `user_pricing.entitlements`.
3. `website/core/pipeline.py` resolves redirects, normalizes the URL, and delegates to `summarization_engine.core.orchestrator`.
4. The summarization engine validates URL safety, detects source type, runs the registered ingestor, rejects near-empty extraction, runs the registered summarizer, and returns the legacy-compatible summary payload.
5. `website/core/persist.py` writes through Supabase v2 when a user/workspace scope is available and updates the file-store graph fallback when appropriate.
6. The entitlement consume step runs only after accepted work.

### Public Graph Read

1. The browser loads `/knowledge-graph` and graph assets from `knowledge_graph/`.
2. The UI fetches `/api/graph`.
3. For an authenticated UUID user, the route first attempts Supabase v2 graph assembly.
4. If v2 is unavailable or misses, it serves the file-store graph from `knowledge_graph/content/graph.json`.
5. The route enriches graph data through `kg_features.analytics.compute_graph_metrics()` and caches the default first page for 30 seconds.

### Signed-In RAG Chat

1. `/home/rag` serves the `user_rag/` static UI.
2. The browser uses `/api/rag/sessions`, `/api/rag/sessions/{id}/messages`, and `/api/rag/adhoc`.
3. The route layer creates a user-scoped runtime with `rag_pipeline.service.get_rag_runtime(user_sub)`.
4. The runtime wires session/Kasten stores, chunk embedding, hybrid retrieval, graph scoring, cascade reranking, context assembly, LLM routing, answer criticism, citation metadata, and post-answer side effects.
5. Rerank admission is bounded; a full queue returns a retryable 503.

### Kasten Management

1. `/home/kastens` serves the Kasten UI from `user_kastens/`.
2. `/api/rag/sandboxes*` handles list, create, update, delete, share, and member operations.
3. The v2 path uses Supabase v2 Kasten tables/RPCs when available.
4. Some tag/source-filtered member operations keep compatibility behavior because the v2 bulk-add RPC accepts explicit workspace zettel IDs.

### Pricing And Entitlements

1. `user_pricing.routes` serves catalog, billing, order, subscription, verification, webhook, and status APIs.
2. Summarize, RAG, and Kasten routes call `require_entitlement()` before work and `consume_entitlement()` after accepted work.
3. Razorpay webhooks are the canonical payment/subscription truth path and verify signatures before dispatch.
4. Browser checkout support is provided by `user_pricing/js/purchase_launcher.js`, but secret-bearing work stays server-side.

### Monitoring And Alerts

1. `web_monitor` exposes lightweight monitoring/webhook routers.
2. `website/app.py` schedules pricing-visit notifications without blocking page rendering.
3. The global exception handler calls `notify_app_error()` and swallows alerting failures so Slack outages do not break user responses.

## Dependencies And External Contracts

- Gemini / Google GenAI: key pooling, summarization, embeddings, RAG generation, metadata extraction, and eval tooling use configured Gemini keys through the shared pool.
- Supabase v2: authenticated RAG, Kasten/sandbox storage, user-scoped graph assembly, billing/profile lookup, persisted zettels, and pricing repositories rely on `website/core/supabase_v2`.
- Razorpay: pricing routes create/verify orders and subscriptions, expose only public key material to the browser, and keep secrets server-side.
- Slack incoming webhooks: `web_monitor` posts app errors, user activity, DigitalOcean alerts, pricing visits, and payment events when matching webhook variables are configured.
- Browser storage: selected UI helpers use `localStorage` or `sessionStorage` for non-secret hints, return paths, avatar URL fallback, and graph view preference. Server auth and entitlements remain authoritative.
- Local model/runtime assets: RAG reranking reads model/runtime settings from environment and model paths; failures can surface as memory pressure or unavailable-reranker behavior.
- Upstream content services: summarization ingestors may call GitHub, Reddit/PullPush, YouTube metadata/transcript tiers, newsletter/web pages, Hacker News, arXiv, podcast pages, Twitter/Nitter, LinkedIn, and generic web fetchers.

## How To Extend Or Modify Safely

- For a new source type, update the summarization-engine model/router/config path, add a `source_ingest/<source>` ingestor, add a `summarization/<source>` summarizer, register both through the existing registry pattern, and add targeted unit/eval coverage.
- For a new authenticated page, add feature assets here, then wire route/static mounts in `website/app.py` and API handlers in `website/api/` or a feature-owned router.
- For RAG changes, preserve UUID-auth requirements, bounded rerank/admission behavior, SSE streaming behavior, citation checks, entitlement preflight/consume pattern, and workspace scoping.
- For pricing changes, keep Razorpay verification, webhook idempotency, billing repository writes, and entitlement accounting aligned. Do not move secret-bearing data into browser JS.
- For browser storage changes, store only non-sensitive hints or display cache. Never make browser storage authoritative for auth, billing, permissions, or server state.
- For graph changes, remember that `knowledge_graph/content/graph.json` is a feature asset, while graph mutation and persistence live in `website/core/graph_store.py`, `website/core/persist.py`, and API routes.
- For UI work, follow the project color rule: no purple, violet, or lavender anywhere; Knowledge Graph accent stays amber/gold and main site accents stay teal.

## Testing And Debugging Notes

- Summarization engine tests live in `website/features/summarization_engine/tests/` and `tests/unit/summarization_engine/`, with caller coverage in `tests/test_website_pipeline_engine.py`.
- API key pooling is covered by `tests/test_key_pool.py`, `tests/test_api_key_pool_env.py`, `tests/test_routing.py`, and `tests/unit/api_key_switching/`.
- KG analytics/embeddings have coverage under `tests/kg_intelligence/` and `tests/unit/test_kg_features_unreachable.py`.
- RAG coverage is spread across `tests/unit/rag/`, `tests/unit/rag_pipeline/`, `tests/integration/rag_pipeline/`, `tests/integration/v2/`, and API route tests such as `tests/test_rag_api_routes.py`.
- Pricing coverage lives under `tests/unit/user_pricing/` plus v2 integration guards such as `tests/integration/v2/test_phase_8_pricing.py`.
- Nexus is not in this folder; its tests live under `tests/unit/experimental_features/` and `tests/integration/v2/`.
- For docs-only edits to this file, run `git diff --check -- website/features/About.md` and read back the changed sections.
- For code changes in this folder, run the narrow tests for the touched feature and avoid live tests unless credentials and `--live` are intentionally in scope.

## Invariants, Gotchas, And Known Risks

- This folder is not an app boundary by itself. `website/app.py`, `website/api/`, and `website/core/` decide how feature code becomes routes, persistence, auth, and middleware.
- `knowledge_graph/content/graph.json` is a public/file-store graph asset and may already be dirty from runtime captures. Do not casually rewrite it during documentation work.
- `/api/graph` anonymous/public fallback is file-store based even when authenticated v2 graph assembly fails.
- `rag_pipeline.service.get_rag_runtime()` requires an authenticated UUID subject.
- RAG and summarization paths are sensitive to environment configuration, external API quota, upstream extractor availability, local model memory pressure, and Supabase v2 scope.
- Several browser features read from storage for convenience, but server APIs must remain authoritative.
- The `web_monitor` package intentionally keeps one self-contained file per Slack channel; add sibling files unless the pattern is deliberately refactored.
- There is currently no `AGENTS.md` in `website/features/`; folder-local guidance is in the existing `CLAUDE.md` files and this `About.md`.

## Related Docs

- `AGENTS.md` / `CLAUDE.md` at the repository root for global project rules, production constraints, testing expectations, and secret handling.
- `website/features/summarization_engine/About.md`
- `website/features/browser_cache/About.md`
- `website/features/rag_pipeline/CLAUDE.md`
- `website/features/summarization_engine/core/CLAUDE.md`
- `website/features/summarization_engine/summarization/CLAUDE.md`
- `website/features/api_key_switching/CLAUDE.md`
- `website/features/user_home/CLAUDE.md`
- `website/features/user_zettels/CLAUDE.md`
- `website/features/user_kastens/CLAUDE.md`
- `website/features/user_rag/CLAUDE.md`
- `website/features/header/CLAUDE.md`
- `website/features/user_pricing/PRICING.md`
- `website/experimental_features/About.md`
