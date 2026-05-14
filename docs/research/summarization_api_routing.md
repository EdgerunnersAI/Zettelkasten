# Add Zettel API Routing

Date: 2026-05-14

## Current Path

All website Add Zettel actions now call one facade:

```http
POST /api/zettels/add
```

The FastAPI facade is implemented in `website/api/zettels_routes.py` and mounted
by `website/app.py`. The actual module runner lives in
`website/api/module_runners/summarization.py`, so the same pipeline can be
invoked by the HTTP route and by CLI verification.

## Contract

Request:

```json
{
  "url": "https://example.com/post",
  "client_action_id": "unique-click-id",
  "persist": true,
  "surface": "landing",
  "mode": "auto"
}
```

`surface` is one of `landing`, `home`, or `zettels`. `mode` is `sync` or
`auto`.

Response:

```json
{
  "status": "succeeded",
  "operation_id": "unique-click-id",
  "summary": {},
  "persistence": {},
  "quality": {},
  "node_id": "web-node",
  "workspace_zettel_id": "uuid-or-null",
  "status_url": null
}
```

Failures use `application/problem+json` with `type`, `title`, `status`,
`detail`, and `instance`. Quota failures preserve the machine-readable
`detail.code = "quota_exhausted"` shape used by the pricing resume UI.

## Runtime Flow

1. Validate URL shape and SSRF safety.
2. Resolve the authenticated user with `get_optional_user`.
3. Use the authenticated UUID when present; otherwise use the canonical Zoro
   UUID from `ops/deploy/expected_users.json`.
4. Require zettel entitlement before network, Gemini, or persistence work.
5. Resolve redirects and normalize the URL.
6. Call `summarize_url_bundle(...)` from the summarization engine.
7. Convert the engine result into the website summary DTO.
8. If `persist=true`, call `website.core.persist.persist_summarized_result`.
9. Consume entitlement only after persistence succeeds.
10. Invalidate graph caches after a persisted write.

## Idempotency

`client_action_id` is the idempotency key, scoped by effective user UUID. The
facade keeps a tiny in-process LRU cache of completed responses. Repeated
clicks with the same key return the original response without re-running
summarization, persistence, or entitlement consumption.

## Slow Path

`mode=auto` waits up to a short threshold. If processing exceeds that threshold,
the facade returns `202 Accepted` with `Location`, `Retry-After`, and
`status_url = /api/operations/{operation_id}`. The background task continues in
process with bounded concurrency; no Redis, Celery, RabbitMQ, or external worker
service is introduced. The shared frontend helper polls the status URL with a
bounded retry window.

## Frontend

All Add Zettel surfaces use `website/static/js/add_zettel_api.js`:

- landing page: `website/static/js/app.js`
- home page: `website/features/user_home/js/home.js`
- My Zettels page: `website/features/user_zettels/js/user_zettels.js`
- mobile landing: `website/mobile/js/summarizer.js`

The helper checks `Content-Type` before parsing JSON. Non-JSON responses are
converted into clean user-facing errors.

## CLI Runner

The Add Zettel runner can be invoked directly:

```bash
python -m website.api.module_runners.summarization \
  --load-env \
  --url "https://example.com/article" \
  --user-id "<supabase-auth-uuid>" \
  --client-action-id "manual-cli-test"
```

The CLI uses the same `run_add_zettel_pipeline(...)` function as the FastAPI
route. It returns the same JSON envelope as `POST /api/zettels/add`.

## Migration Notes

The deprecated website summarize route and legacy pipeline adapter were removed.
Live website Add Zettel flows no longer depend on the legacy response shape.

The summarization engine API under `/api/v2/summarize` remains available as an
engine-specific interface. It is not the website Add Zettel contract.
