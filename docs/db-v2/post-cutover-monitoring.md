# DB v2 Post-Cutover Monitoring

## First 24 Hours

- `/api/health` status and event-loop lag.
- Summarize success rate and latency.
- RAG chat success rate, empty-scope rate, and retrieval latency.
- `content.search_chunks` RPC errors by SQLSTATE.
- `core.consume_quota` false/unauthorized counts.
- Registry adapter refresh failures and LISTEN reconnects.
- Caddy 5xx rate and upstream timeouts.

## Tripwires

- Any sustained 5xx increase after `DB_SCHEMA_VERSION=v2`.
- Unauthorized errors for a workspace present in JWT `app_metadata.workspace_ids`.
- Canonical-content duplicate rate above expected content-hash collisions.
- HNSW search returning empty results for known populated workspaces.
- Billing entitlement consumption failing closed for paid users.

## Daily Checks Until Day +14

- Confirm v1 fallback image/env path remains deployable.
- Confirm `_legacy_*` tables remain present.
- Compare v2 row counts against expected migrated totals.
- Review slow queries on `content.search_chunks`, `core.usage_events`, and RLS-heavy tables.

