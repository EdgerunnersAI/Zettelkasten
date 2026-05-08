# DB v2 Rollback Runbook

## Same-Day Rollback

1. Enable maintenance mode.
2. Set `DB_SCHEMA_VERSION=v1`.
3. Redeploy current image with v1 routing.
4. Smoke test `/api/health`, `/api/graph`, summarize, pricing, and RAG chat.
5. Disable maintenance mode if the v1 path is healthy.

## PITR Rollback

Use PITR only if v1 routing cannot read viable legacy data.

1. Keep maintenance mode enabled.
2. Restore Supabase to the pre-cutover timestamp.
3. Keep `DB_SCHEMA_VERSION=v1`.
4. Redeploy and run smoke tests.
5. Notify affected users if any captures occurred after the restore point.

## Legacy Retention

Do not drop `_legacy_*` tables during rollback. Legacy tables are retained for 14 stable days after cutover and dropped only by the Day +14 cleanup task.

