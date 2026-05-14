# Summarization Engine

Folder-scoped developer notes for `website/features/summarization_engine`, the v2 URL ingestion, summarization, batch, writer, dashboard, and evaluation subtree used by the website pipeline and `/api/v2/*` endpoints.

## What this folder owns

- Source type detection support through `core/router.py` and the `SourceType` enum in `core/models.py`.
- Source ingestion adapters under `source_ingest/`, each returning a canonical `IngestResult`.
- Source summarizers under `summarization/`, each turning an `IngestResult` into a canonical `SummaryResult`.
- The single-URL orchestration path in `core/orchestrator.py`.
- `/api/v2` FastAPI routes, request/response models, batch processing, optional writers, and the static dashboard assets mounted by `website/app.py`.
- Summary-quality evaluation helpers under `evaluator/`, plus local tests for the engine subtree.

## What this folder does not own

- Website-specific Add Zettel request shaping and persistence; `website/api/zettels_routes.py` handles those and delegates to `summarize_url_bundle()`.
- The API-key pool implementation; this engine consumes `website/features/api_key_switching`.
- Knowledge-graph storage policy outside explicit writers. The orchestrator does not persist summaries; callers choose writers.
- Supabase schema ownership, auth policy, pricing, chat, or RAG runtime behavior.
- Production deployment knobs and blue/green infrastructure.

## Key files and subfolders

- `config.yaml` - engine, Gemini, source, batch, writer, logging, and rate-limit configuration loaded by `core/config.py`.
- `core/models.py` - `SourceType`, `IngestResult`, `SummaryResult`, metadata, and batch models.
- `core/orchestrator.py` - validates URLs, detects source type, selects an ingestor and summarizer, caches ingest output, rejects thin content, and returns `OrchestratedSummary`.
- `core/router.py` - URL-to-`SourceType` detection plus YouTube format and GitHub archetype helpers.
- `core/gemini_client.py` and `core/client_factory.py` - tiered Gemini client wrapper and default key-pool/config wiring.
- `core/cache.py`, `core/errors.py`, `core/model_factory.py`, `core/telemetry.py` - shared cache, exception, dynamic model, and tracing utilities.
- `source_ingest/` - auto-discovered ingestors for GitHub, newsletter, Reddit, YouTube, Hacker News, LinkedIn, arXiv, podcast, Twitter, and generic web.
- `summarization/` - auto-discovered summarizers and source-specific schemas, prompts, layout builders, classifiers, and common structured-output utilities.
- `api/` - `/api/v2/summarize`, `/api/v2/batch`, `/api/v2/batch/upload`, and `/api/v2/batch/stream`.
- `batch/` - CSV/JSON loading, bounded-concurrency processing, and SSE progress events.
- `writers/` - `BaseWriter`, `SupabaseWriter`, and Markdown rendering.
- `evaluator/` - rubric loading, consolidated LLM evaluation, numeric grounding, atomic facts, RAGAS bridge, score models, and manual-review output.
- `ui/` - dashboard HTML/CSS/JS served at `/summarization-engine`.
- `tests/` - engine-local unit, integration, and live tests.

## Entry points and public interfaces

- Library:
  - `core.orchestrator.summarize_url(url, user_id, gemini_client, source_type=None) -> SummaryResult`
  - `core.orchestrator.summarize_url_bundle(...) -> OrchestratedSummary`
  - `source_ingest.get_ingestor(source_type)` and `summarization.get_summarizer(source_type)`
  - `batch.processor.BatchProcessor(user_id, gemini_client, writers=[]).run(...)`
  - `writers.base.BaseWriter.write(result, user_id=...)`
- HTTP:
  - `api.routes.router` is an `APIRouter(prefix="/api/v2")` mounted by `website/app.py`.
  - `/api/v2/summarize` returns `SummarizeV2Response`; it writes to Supabase only when `write_to_supabase` is true.
  - `/api/v2/batch` accepts URL lists, `/api/v2/batch/upload` accepts uploaded CSV/JSON, and `/api/v2/batch/stream` wraps batch results in SSE progress events.
- Website Add Zettel caller:
  - `website/api/zettels_routes.py` resolves redirects, normalizes the URL, delegates to `summarize_url_bundle()`, persists through `website.core.persist`, then returns the Add Zettel response envelope.

## Representative runtime flows

### Single URL through `/api/v2/summarize`

1. `api.routes.summarize_v2()` validates the request model and resolves the optional authenticated user.
2. `_gemini_client()` loads Gemini keys from environment variables or the configured key-file candidates, then constructs `TieredGeminiClient(GeminiKeyPool(keys), load_config())`.
3. `core.orchestrator.summarize_url()` calls `summarize_url_bundle()`.
4. The orchestrator blocks invalid or private URLs with `validate_url()`, loads config, detects the source type unless one was supplied, and instantiates the registered ingestor.
5. Ingest output is cached by `(url, ingestor.version, source_type)`.
6. Low-confidence extraction is logged; near-empty extracted content raises `ExtractionConfidenceError` before Gemini summarization.
7. The registered summarizer runs source-specific or default summarization and returns `SummaryResult`.
8. If `write_to_supabase` is true, `SupabaseWriter.write()` persists the result and returns writer metadata.

### Add Zettel facade

`website/api/zettels_routes.py` is the website-native facade for Add Zettel. It resolves authenticated users, maps unauthenticated captures to the Zoro user, calls `summarize_url_bundle()`, persists through `persist_summarized_result()`, and returns a stable JSON envelope.

### Batch

`BatchProcessor.run()` loads CSV or JSON input, builds a `BatchRun`, processes URLs with a bounded worker queue using `config.yaml` `batch.max_concurrency`, records per-item success or failure, and returns run metadata plus item results. Writers are applied per successful item only when supplied by the caller.

### Evaluation loop

The engine evaluator is used by `ops/scripts/eval_loop.py` and `ops/scripts/lib/phases.py`. Current eval assets live under `docs/summary_eval/` for GitHub, newsletter, Reddit, and YouTube, with shared config/cache folders and `RUNBOOK_CODEX.md`. `docs/testing/links.txt` is a URL input source for the loop.

## Dependencies and external contracts

- URL safety depends on `utils/url_utils.validate_url()` and, for the legacy path, redirect/normalization helpers in the same module.
- Gemini calls go through `TieredGeminiClient` and `GeminiKeyPool`; missing keys make the HTTP route return 503.
- `config.yaml` values are parsed into `EngineConfig`; tests that mutate config should call `reset_config_cache()`.
- Ingestors may call external services: GitHub API, Reddit JSON/HTML plus PullPush, YouTube transcript/metadata tiers, newsletter HTML providers and archive fallbacks, Hacker News, arXiv, podcast lookup, Twitter/Nitter, and generic web fetchers.
- Supabase persistence is an optional writer concern, not an orchestrator concern.
- Structured summary caps should be enforced through `core/model_factory.build_summary_result_model(cfg)` and the structured extractor, not by ad hoc truncation in callers.

## How to extend safely

1. Add the new value to `SourceType` in `core/models.py`.
2. Add routing rules in `core/router.py`; unknown or malformed URLs should continue to fall back to `SourceType.WEB`.
3. Add source config under `config.yaml`.
4. Add `source_ingest/<source>/ingest.py` with a `BaseIngestor` subclass that sets `source_type` and returns a complete `IngestResult`.
5. Add `summarization/<source>/summarizer.py` with a `BaseSummarizer` subclass that sets the same `source_type` and returns a `SummaryResult`.
6. Add source schemas, prompts, layouts, and focused tests when the default summarizer is not enough.
7. Verify registry auto-discovery with `list_ingestors()` and `list_summarizers()` or route/orchestrator tests.
8. Add or update eval rubrics/assets when the source is part of the quality loop.

Do not make the orchestrator write to storage, bypass the ingestor/summarizer registries, or summarize thin content by weakening the `ExtractionConfidenceError` gate.

## Testing and debugging notes

- Engine-local tests live in `website/features/summarization_engine/tests/`.
- Broader unit coverage lives in `tests/unit/summarization_engine/`, with caller coverage in `tests/test_website_pipeline_engine.py`, writer/persist tests under `tests/unit/website/`, and eval-loop tests under `tests/eval/`.
- Live engine coverage is in `website/features/summarization_engine/tests/live/test_live_engine.py` and requires real Gemini credentials.
- Useful focused commands:
  - `pytest website/features/summarization_engine/tests/unit -v`
  - `pytest tests/unit/summarization_engine -v`
  - `pytest tests/test_website_pipeline_engine.py -v`
  - `pytest tests/eval -v`
- For 422-style extraction failures, inspect `ExtractionConfidenceError.tier_results` when present; YouTube ingestors preserve per-tier diagnostics in metadata.
- For model fallback investigations, inspect `SummaryMetadata.model_used` and `fallback_reason`; default-path summarizers thread DenseVerify and structured-extraction call traces into metadata.

## Invariants, gotchas, and known risks

- `IngestResult.raw_text` must contain grounded source material. The summarizers should not invent around missing extraction.
- `IngestResult.extraction_confidence == "low"` is logged but not automatically fatal; near-empty content is fatal.
- The orchestrator is a pure library with respect to persistence. HTTP routes and batch processors compose writers explicitly.
- Registry discovery imports `<package>.<source>.ingest` and `<package>.<source>.summarizer`; module naming matters.
- `SummaryResult` in `core/models.py` is a compatibility type. Dynamic caps come from `build_summary_result_model(cfg)`.
- The structured extractor may fall back to a valid minimal payload tagged `_schema_fallback_` when schema repair fails; treat that as a quality signal, not success parity.
- `BatchProcessor` catches per-item exceptions and reports failed items instead of failing the whole run.
- YouTube transcript extraction can fail from datacenter environments. The current ingestor uses transcript/metadata tiers and marks metadata-only extraction as low confidence.
- Newsletter ingestion can raise `NewsletterURLUnreachable` before Gemini when preflight proves the URL is dead or unreachable.
- Some source packages expose compatibility classes in `__init__.py`, but auto-discovery uses the `ingest.py` and `summarizer.py` modules.

## Related docs

- `AGENTS.md` / `CLAUDE.md` - project rules, production discipline, commands, and high-level architecture.
- `README.md` - user-facing product overview and builder handoff.
- `docs/superpowers/specs/2026-04-10-summarization-engine-v2-design.md` - original v2 design context; verify against code before relying on it.
- `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md` - evaluation and scoring-loop design context.
- `docs/summary_eval/RUNBOOK_CODEX.md` - current summary-evaluation workflow.
