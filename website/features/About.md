# Website Feature Layer

`website/features/` owns feature-specific UI assets, domain services, and feature routers used by the FastAPI website. It is the layer where product capabilities live; `website/app.py`, `website/api/`, and `website/core/` compose those capabilities into the running app.

## What This Folder Owns

- Feature-owned static pages and browser assets mounted by `website/app.py`, including the knowledge graph, auth callback, signed-in home, zettels, kastens, RAG chat, shared header, browser-cache helper, pricing launcher assets, and the summarization-engine dashboard.
- Feature-owned Python packages for summarization, Gemini key pooling, KG analytics/embeddings, RAG retrieval/chat, pricing/payments/entitlements, and Slack-backed web monitoring.
- Registries and extension points inside the summarization engine: source ingestors, source-specific summarizers, batch processors, writers, evaluator helpers, and v2 API models/routes.
- RAG runtime components: query rewrite/routing, retrieval, reranking, context assembly, LLM backends, sessions, kastens/sandboxes, ingestion/chunking, evaluation, and observability.
- Feature-local docs and guardrails in sibling `CLAUDE.md` / `About.md` files.

## What This Folder Does Not Own

- FastAPI app assembly, global middleware, route mounting, and HTML shell injection; those are in `website/app.py`.
- The legacy/public API route definitions for `/api/summarize`, `/api/graph`, zettel mutation, graph query/search, RAG chat, and kasten CRUD; most of those handlers live in `website/api/`, although they call feature services.
- Core persistence, graph file-store behavior, Supabase v2 repository clients, auth dependencies, settings, and database schema ownership; those live under `website/core/`, `website/api/auth.py`, and `supabase/`.
- Mobile routes/assets, footer about/pricing pages, experimental Nexus features, deployment configuration, and root project documentation.
- Secret storage. Feature code reads configured environment variables, but this folder must not contain real `.env` material or committed credentials.

## Key Files And Subfolders

- `api_key_switching/` - `GeminiKeyPool`, key discovery, retry/cooldown behavior, embedding calls, and content-routing helpers used by summarization, KG embeddings, and RAG.
- `browser_cache/` - small browser-side helper for safe auth/return-path state in `localStorage` and `sessionStorage`; it is not an auth source of truth.
- `header/` - shared header fragment plus CSS/JS. `website/app.py` injects `header.html` through `_render_with_shell`.
- `knowledge_graph/` - static graph page, CSS/JS, modal JS, and `content/graph.json`, which remains the public/file-store graph fallback served through `/kg/*` and `/api/graph`.
- `kg_features/` - graph analytics and embedding utilities. `/api/graph` enriches graph responses with `compute_graph_metrics`; persistence can use `generate_embedding`.
- `rag_pipeline/` - authenticated RAG runtime and support modules: query, retrieval, rerank, context, generation, memory, ingest, scoring, observability, evaluation, and adapters.
- `summarization_engine/` - v2 URL summarization engine, including config, API routes, batch handling, source ingestion, summarizers, writers, evaluator tooling, dashboard assets, and feature-local tests.
- `user_auth/` - auth callback page plus client CSS/JS for Supabase-backed login state handling.
- `user_home/`, `user_zettels/`, `user_kastens/`, `user_rag/` - signed-in static UI surfaces. Their APIs are mostly in `website/api/`, backed by RAG and persistence services.
- `user_pricing/` - pricing catalog, entitlement checks, Razorpay client, repository access, payment/subscription routes, models, config, docs, and checkout launcher assets.
- `web_monitor/` - Slack notification helpers and routers for application errors, DigitalOcean alerts, user activity, pricing visits, and payment notifications.

## Entry Points And Public Interfaces

- `website/app.py:create_app()` includes feature routers from `summarization_engine.api`, `user_pricing.routes`, and `web_monitor`; it also mounts feature static assets under `/kg/*`, `/auth/*`, `/browser-cache/*`, `/home/*`, `/user-pricing/*`, `/header/*`, and `/summarization-engine/*`.
- `website/api/routes.py` exposes `/api/summarize`, `/api/graph`, `/api/zettels/{node_id}`, `/api/graph/query`, and `/api/graph/search`; it calls `website.core.pipeline`, `website.core.persist`, `kg_features.analytics`, and `user_pricing.entitlements`.
- `website/api/chat_routes.py` exposes `/api/rag/*` chat/session endpoints and streams answers through `rag_pipeline.service.get_rag_runtime()`.
- `website/api/sandbox_routes.py` exposes `/api/rag/nodes` and `/api/rag/sandboxes*` kasten/sandbox endpoints backed by `rag_pipeline.memory` plus Supabase v2 repository calls.
- `summarization_engine.api.routes` exposes `/api/v2/summarize`, `/api/v2/batch`, `/api/v2/batch/upload`, and `/api/v2/batch/stream`.
- `user_pricing.routes` exposes pricing catalog, billing profile, payment order/subscription, verification, webhook, subscription status, and payment status endpoints.
- `web_monitor.__init__` exports an aggregate router plus `notify_app_error`, `notify_new_signup`, `notify_pricing_visit`, and `notify_payment`.
- `api_key_switching.__init__` exports `init_key_pool()` and `get_key_pool()` as the shared Gemini key-pool singleton accessors.
- `summarization_engine.source_ingest` and `summarization_engine.summarization` expose `register_*`, `get_*`, and `list_*` registry functions for ingestors and summarizers.

## Representative Runtime Flows

1. Public or signed-in URL capture:
   `/api/summarize` validates and rate-limits the URL, checks a zettel entitlement, calls `website.core.pipeline.summarize_url()`, then persists through `website.core.persist.persist_summarized_result()`. The pipeline delegates to `summarization_engine.core.orchestrator`, which validates the URL, detects source type, runs the registered ingestor, rejects near-empty extraction, runs the registered summarizer, and returns a legacy-compatible result.

2. Public graph read:
   `/api/graph` tries Supabase v2 assembly for an authenticated UUID user. If v2 is unavailable or misses, it serves the file-store graph and enriches it through `kg_features.analytics.compute_graph_metrics()`. The default first page is cached in memory for 30 seconds.

3. Signed-in RAG chat:
   `/home/rag` serves the static UI. The browser talks to `/api/rag/sessions`, `/api/rag/sessions/{id}/messages`, and `/api/rag/adhoc`. The route layer creates a user-scoped runtime with `get_rag_runtime(user_sub)`, which requires a UUID auth subject and wires sessions, kastens/sandboxes, chunk embedding, hybrid retrieval, graph scoring, cascade reranking, context assembly, LLM routing, answer criticism, and metadata extraction.

4. Kasten management:
   `/home/kastens` serves the UI. `/api/rag/sandboxes*` handles list/create/update/delete/share/member operations. The v2 path uses Supabase v2 kasten tables/RPCs when available; some tag/source-filtered member operations intentionally keep a compatibility path because the v2 bulk-add RPC accepts explicit workspace zettel IDs.

5. Pricing and entitlements:
   `user_pricing.routes` serves catalog/billing/payment APIs. Summarize, RAG, and kasten routes call `require_entitlement()` before work and `consume_entitlement()` after accepted work. Razorpay webhook handling is the canonical payment/subscription truth path and verifies signatures before dispatch.

6. Monitoring:
   `web_monitor` routers expose health/webhook endpoints and post Slack messages when configured. `website/app.py` schedules pricing-visit notifications and uses `notify_app_error()` from the global exception handler without letting alert failures break the response path.

## Dependencies And External Contracts

- Gemini / Google GenAI: `api_key_switching`, summarizers, embeddings, and RAG generation/metadata extraction use configured Gemini keys through the shared pool.
- Supabase v2: authenticated RAG, kasten/sandbox storage, user-scoped graph assembly, billing/profile lookup, and persisted zettels rely on the v2 client/repositories in `website/core/supabase_v2`.
- Razorpay: `user_pricing` creates/verifies orders and subscriptions, exposes the public key ID to the client, and keeps secrets server-side.
- Slack incoming webhooks: `web_monitor` posts app errors, user activity, DigitalOcean alerts, pricing visits, and payment events when the matching webhook variables are configured.
- Browser storage: selected UI helpers use `localStorage` or `sessionStorage` for UI hints, return paths, avatar URL fallback, and graph view preference. Server auth and entitlement checks still come from API dependencies.
- Local model/runtime files: RAG reranking reads model/runtime settings from environment and model paths; failures can surface as memory pressure or unavailable-reranker behavior in API routes.
- Network extractors: summarization ingestors may call upstream services such as YouTube metadata/transcript tiers, GitHub APIs, newsletter/web pages, Reddit/PullPush, arXiv, Twitter/Nitter, Hacker News, and podcast sources depending on source type.

## How To Extend Safely

- For a new source type, update the summarization-engine model/router/config path, add a `source_ingest/<source>` ingestor, add a `summarization/<source>` summarizer, register both through the existing registry pattern, and add unit coverage under `tests/unit/summarization_engine` or `website/features/summarization_engine/tests`.
- Keep extraction confidence meaningful. The orchestrator rejects low-content results before Gemini summarization to reduce hallucinated summaries.
- For new authenticated UI pages, add only the feature assets here, then wire routes/static mounts in `website/app.py` and API handlers in `website/api/` or a feature-owned router as appropriate.
- For RAG changes, preserve the authenticated UUID subject requirement, bounded rerank/admission behavior, SSE heartbeat behavior, citation checks, and entitlement preflight/consume pattern.
- For pricing changes, keep Razorpay verification, webhook idempotency, and entitlement accounting together. Do not move secret-bearing data into browser JS.
- For browser storage changes, store only non-sensitive hints or cached display data; never make client storage authoritative for auth, billing, or permissions.
- For UI work, follow the project color rule: no purple/violet/lavender anywhere; Knowledge Graph accent stays amber/gold and main site accents stay teal.

## Testing And Debugging Notes

- Summarization engine has feature-local tests in `website/features/summarization_engine/tests/` plus broader coverage in `tests/unit/summarization_engine/`.
- API key pooling is covered by `tests/test_key_pool.py`, `tests/test_api_key_pool_env.py`, `tests/test_routing.py`, and `tests/unit/api_key_switching/`.
- KG analytics/embeddings have tests under `tests/kg_intelligence/` and `tests/unit/test_kg_features_unreachable.py`.
- RAG coverage is spread across `tests/unit/rag/`, `tests/unit/rag_pipeline/`, `tests/integration/rag_pipeline/`, `tests/integration/v2/`, and API route tests such as `tests/test_rag_api_routes.py`.
- Pricing coverage lives under `tests/unit/user_pricing/` plus v2 integration guards such as `tests/integration/v2/test_phase_8_pricing.py`.
- For docs-only edits to this file, a diff review is usually enough. For code changes in this folder, run the narrow tests for the touched feature and avoid live tests unless credentials and `--live` are intentionally in scope.

## Invariants, Gotchas, And Known Risks

- `website/features/About.md` is documentation only; changing it must not imply runtime ownership that the code does not have.
- `knowledge_graph/content/graph.json` is a feature asset but graph persistence and mutation are owned by `website/core/graph_store.py`, `website/core/persist.py`, and API routes.
- `/api/graph` anonymous/public fallback is file-store based even when authenticated v2 graph assembly fails.
- `rag_pipeline.service.get_rag_runtime()` fails without an authenticated UUID subject.
- RAG and summarization paths are sensitive to environment configuration, external API quota, upstream extractor availability, and local model memory pressure.
- Several browser features read from `localStorage` for convenience, but server APIs must remain authoritative.
- The `web_monitor` package intentionally keeps one self-contained file per Slack channel; add sibling files rather than creating a shared base unless the pattern changes deliberately.
- Do not edit `website/features/AGENTS.md`; there is no such canonical file. Folder-local rules are in the existing `CLAUDE.md` files.

## Related Docs

- `AGENTS.md` / `CLAUDE.md` at the repo root for global project rules, deployment constraints, and secret handling.
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
