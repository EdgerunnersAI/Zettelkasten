# Experimental Features

`website/experimental_features/` contains feature tracks that are not part of the stable `website/features/` product surface, but some code here is still wired into the running FastAPI app.

## What This Folder Owns

- `nexus/`: the experimental provider-connection and bulk-import surface for pulling saved artifacts from external services into the user's Kasten.
- `PageIndex_Rag/`: a local, CLI-first PageIndex-backed RAG evaluation harness.
- Folder-local tests for `PageIndex_Rag` under `PageIndex_Rag/pytests/`.
- Folder-local developer notes for Nexus subareas through nested `CLAUDE.md` files.

## What This Folder Does Not Own

- The core URL summarization pipeline. Nexus calls the summarization engine rather than implementing summarization itself.
- Canonical persistence for summarized results. Nexus uses `website.core.persist.persist_summarized_result()` through the compatibility shim in `nexus/service/persist.py`.
- The production RAG pipeline under `website/features/rag_pipeline/`.
- Global FastAPI app setup, auth dependencies, Supabase clients, or database schema migrations.
- Secret files or real credentials. Do not read `nexus/nexus_env.txt` or any `.env*` file while documenting this folder.

## Key Files And Subfolders

- `About.md`: this folder-scoped developer map.
- `nexus/index.html`: Nexus page rendered at `/home/nexus` when Nexus is enabled.
- `nexus/css/nexus.css` and `nexus/js/nexus.js`: Nexus static assets mounted at `/home/nexus/css` and `/home/nexus/js`.
- `nexus/service/bulk_import.py`: provider import orchestration; writes Nexus runs/items to the Supabase v2 `pipelines` schema.
- `nexus/service/token_store.py`: encrypted OAuth token storage backed by `pipelines.nexus_provider_tokens`.
- `nexus/service/persist.py`: backward-compatible re-export shim for `website.core.persist`.
- `nexus/source_ingest/common/`: shared provider models, OAuth state, and OAuth utility helpers.
- `nexus/source_ingest/{github,reddit,twitter,youtube}/`: provider-specific OAuth and artifact-ingest modules.
- `PageIndex_Rag/cli.py`: local eval entry point.
- `PageIndex_Rag/config.py`: `PAGEINDEX_RAG_*` config loader.
- `PageIndex_Rag/pipeline.py`: PageIndex query flow: index, select candidates, retrieve evidence, generate answers.
- `PageIndex_Rag/data_access.py`: retired legacy data-access module; any attribute access raises `NotImplementedError`.
- `PageIndex_Rag/pytests/`: direct tests for the PageIndex harness.

## Entry Points And Public Interfaces

- `website.app.create_app()` sets `NEXUS_DIR` to this folder and enables Nexus unless `NEXUS_ENABLED` is `0`, `false`, `no`, or `off`.
- When enabled, `create_app()` includes `website.api.nexus.router`, mounts Nexus CSS/JS, and serves `/home/nexus`.
- `website.api.nexus` exposes `/api/nexus/providers`, `/api/nexus/connect/{provider}`, `/api/nexus/callback/{provider}`, `/api/nexus/disconnect/{provider}`, `/api/nexus/import/{provider}`, `/api/nexus/import/all`, and `/api/nexus/runs`.
- `nexus/js/nexus.js` calls those `/api/nexus/*` endpoints from the browser.
- Provider modules are loaded dynamically by provider value and expected module shape: `source_ingest.{provider}.oauth` for OAuth and `source_ingest.{provider}.ingest` for import handlers.
- `PageIndex_Rag.cli.main()` runs the local PageIndex eval path; it is not mounted as a FastAPI route.

## Representative Runtime Flows

Nexus page load:

1. A desktop user requests `/home/nexus`.
2. `website.app.create_app()` serves `nexus/index.html` through the shared shell renderer if `NEXUS_ENABLED` allows it.
3. `nexus/js/nexus.js` loads provider descriptors from `/api/nexus/providers` and recent runs from `/api/nexus/runs`.

Nexus connect flow:

1. The browser posts to `/api/nexus/connect/{provider}`.
2. `website.api.nexus` imports the provider OAuth module and calls the first supported connect handler.
3. Provider OAuth code issues short-lived in-memory state through `common/oauth_state.py`.
4. The provider redirects back to `/api/nexus/callback/{provider}`.
5. The callback exchanges or normalizes tokens, then `upsert_provider_account()` stores them through `ProviderTokenStore`.

Nexus import flow:

1. The browser posts to `/api/nexus/import/{provider}` or `/api/nexus/import/all`.
2. `bulk_import.run_provider_import()` resolves the authenticated profile and default workspace, fetches the stored provider account, and creates a `pipelines.pipeline_runs` row with `kind="nexus_ingest"`.
3. The provider ingest handler returns `ProviderArtifact` records.
4. Each artifact is skipped if already imported, otherwise summarized with `summarize_url()` and persisted with `persist_summarized_result()`.
5. Per-artifact results are written to `pipelines.pipeline_run_items`, and the run status is finalized as `completed`, `partial_success`, or `failed`.

PageIndex eval flow:

1. `PageIndex_Rag.cli.run_eval()` requires `PAGEINDEX_RAG_ENABLED=true` and `PAGEINDEX_RAG_MODE=local`.
2. It loads login details, fixture queries, and scoped zettels.
3. `PageIndexWorkspace` renders markdown, indexes the whole Kasten and each zettel, and caches manifests under the configured workspace.
4. `pipeline.answer_query()` selects candidates, retrieves evidence through the adapter, and generates answer candidates through the Gemini key pool.
5. The CLI writes eval artifacts such as `queries.json`, `kasten.json`, `index_manifest.json`, `answers.json`, `README.md`, and `run.log` to the configured eval directory.

## Dependencies And External Contracts

- Nexus requires Supabase v2 to be configured for API routes that list providers or persist imports.
- OAuth token encryption depends on `NEXUS_TOKEN_ENCRYPTION_KEY` and `cryptography.fernet.Fernet`.
- Nexus token rows use `pipelines.nexus_provider_tokens` with `(profile_id, provider)` as the upsert conflict target.
- Nexus import runs use `pipelines.pipeline_runs`; import items use `pipelines.pipeline_run_items`.
- Supported Nexus providers are `youtube`, `github`, `reddit`, and `twitter`.
- Provider OAuth config reads `NEXUS_GITHUB_*`, `NEXUS_REDDIT_*`, `NEXUS_TWITTER_*`, and `NEXUS_YOUTUBE_*` environment variables.
- OAuth state is process-local, capped, and short-lived; a restart or blue/green flip can invalidate a pending connect attempt.
- PageIndex config is controlled by `PAGEINDEX_RAG_*` environment variables. `PAGEINDEX_RAG_PAGEINDEX_API_KEY` is a secret value if set.
- PageIndex generation uses `website.features.api_key_switching.get_key_pool()`.

## How To Extend Safely

- Keep each experiment isolated behind explicit routes, flags, or CLI entry points.
- For a new Nexus provider, add the provider enum value, implement `source_ingest/{provider}/oauth.py` and `ingest.py`, and update tests around dynamic handler discovery, connection, import, and disconnect behavior.
- Provider ingest handlers should return normalized `ProviderArtifact` objects with stable `external_id` and `url` values.
- Do not bypass `summarize_url()` or `persist_summarized_result()` for Nexus imports unless the production persistence contract is intentionally changed elsewhere.
- Preserve `ImportRequest.limit` validation: accepted limits are 1 through 100.
- Treat token metadata round-trips carefully: the v2 token table persists token fields, not all display metadata from `StoredProviderAccount`.
- Keep PageIndex changes CLI-first and out of the production RAG route until replacement-grade eval evidence exists.
- Follow the global UI rule: no purple, violet, or lavender in Nexus UI assets.

## Testing And Debugging Notes

- Nexus unit coverage lives primarily in `tests/unit/experimental_features/test_nexus_v2.py`, plus provider-focused tests such as `tests/test_nexus_provider_oauth.py`, `tests/test_nexus_youtube_oauth.py`, `tests/test_nexus_token_store.py`, and `tests/test_nexus_bulk_import.py`.
- Nexus v2 integration coverage includes `tests/integration/v2/test_nexus_v2.py` and token RLS tests under `tests/integration/v2/`.
- PageIndex harness tests live in `website/experimental_features/PageIndex_Rag/pytests/` and `tests/unit/experimental_features/test_pageindex_retired.py`.
- Useful focused commands:
  - `pytest tests/unit/experimental_features/test_nexus_v2.py -v`
  - `pytest tests/test_nexus_provider_oauth.py tests/test_nexus_token_store.py tests/test_nexus_bulk_import.py -v`
  - `pytest website/experimental_features/PageIndex_Rag/pytests -v`
  - `python -m compileall website/api/nexus.py website/experimental_features/nexus/service website/experimental_features/nexus/source_ingest`
  - `node --check website/experimental_features/nexus/js/nexus.js`
- If `/home/nexus` is missing, check `NEXUS_ENABLED` and whether `nexus/index.html` exists.
- If token reads fail after deploy, verify that `NEXUS_TOKEN_ENCRYPTION_KEY` matches the key used to encrypt existing rows.

## Invariants, Gotchas, And Known Risks

- `NEXUS_ENABLED` defaults to enabled; disabling is opt-out.
- Nexus is experimental by path, but its app route and API routes are live when enabled.
- OAuth state is not shared across workers or colors; users may need to retry connect after a restart or cutover.
- `PageIndex_Rag.data_access` is intentionally retired and should not be revived without aligning to the v2 workspace/RLS model.
- `PageIndex_Rag.cli.run_eval()` writes artifacts to disk and reads login details; keep it local/eval-only.
- `nexus/IMPLEMENTATION_SPEC.md` describes older Nexus schema expectations and is not fully aligned with the current v2 `pipelines` implementation.

## Related Docs

- `AGENTS.md` / `CLAUDE.md`: project-wide production, testing, memory, secrets, and UI rules.
- `website/experimental_features/PageIndex_Rag/README.md`: PageIndex replacement-track note.
- `docs/superpowers/specs/2026-04-30-pageindex-rag-design.md`: PageIndex RAG design reference named by the PageIndex README.
- `website/experimental_features/nexus/IMPLEMENTATION_SPEC.md`: historical Nexus plumbing spec; verify against current code before relying on it.
- `website/experimental_features/nexus/EXECUTION_PLAN.md`: historical Nexus execution plan.
